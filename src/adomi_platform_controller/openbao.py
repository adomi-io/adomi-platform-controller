"""OpenBao KV v2 access through hvac.

hvac is the standard HashiCorp Vault client, and OpenBao is API-compatible, so the
same client drives both. This wraps hvac with the small amount of behavior the
controller needs: a pluggable token source, a single retry that forces a fresh
token after a 401/403, and a generate-once helper that never overwrites existing
keys.
"""

from __future__ import annotations

from typing import Callable

import hvac
from hvac.exceptions import Forbidden, InvalidPath, Unauthorized

# A token source. ``force_refresh`` asks for a freshly minted token, which the
# client requests after a 401/403 so a revoked or expired token is replaced
# immediately instead of failing every reconcile until it expires.
TokenFunc = Callable[[bool], str]

_TIMEOUT = 15  # seconds


class OpenBaoError(RuntimeError):
    """An OpenBao request failed."""


class OpenBaoClient:
    """Reads and writes a single OpenBao KV v2 mount."""

    def __init__(self, addr: str, mount: str, token: TokenFunc) -> None:
        self._mount = mount.strip("/")
        self._token = token
        self._client = hvac.Client(url=addr.rstrip("/"), timeout=_TIMEOUT)

    @classmethod
    def static(cls, addr: str, mount: str, token: str) -> "OpenBaoClient":
        """Return a client backed by a fixed token."""
        return cls(addr, mount, lambda _force: token)

    def read(self, path: str) -> dict[str, str] | None:
        """Return the key/value map stored at the path, or None when absent."""
        for attempt in range(2):
            self._client.token = self._token(attempt > 0)
            try:
                resp = self._client.secrets.kv.v2.read_secret_version(
                    path=path.strip("/"),
                    mount_point=self._mount,
                    raise_on_deleted_version=True,
                )
                return resp["data"]["data"] or {}
            except InvalidPath:
                return None
            except (Forbidden, Unauthorized):
                if attempt == 0:
                    continue
                raise OpenBaoError(f"openbao read {path}: access denied")
        raise OpenBaoError(f"openbao read {path}: failed after token refresh")

    def write(self, path: str, data: dict[str, str]) -> None:
        """Store the given key/value map at the path (KV v2 create/update).

        Replaces the data at the path with exactly the provided map; callers that
        need to preserve existing keys should read-merge-write (see ensure_keys).
        """
        for attempt in range(2):
            self._client.token = self._token(attempt > 0)
            try:
                self._client.secrets.kv.v2.create_or_update_secret(
                    path=path.strip("/"), secret=data, mount_point=self._mount
                )
                return
            except (Forbidden, Unauthorized):
                if attempt == 0:
                    continue
                raise OpenBaoError(f"openbao write {path}: access denied")
        raise OpenBaoError(f"openbao write {path}: failed after token refresh")

    def ensure_keys(
        self,
        path: str,
        want: list[str],
        gen: Callable[[str], str],
    ) -> tuple[dict[str, str], bool]:
        """Guarantee each key in ``want`` exists at ``path``, generating missing values.

        Existing keys are never overwritten ("generate once, never regenerate").
        Returns the full resulting key/value map and whether a write occurred.
        """
        current = self.read(path) or {}
        changed = False

        for key in want:
            if current.get(key):
                continue
            current[key] = gen(key)
            changed = True

        if changed:
            self.write(path, current)

        return current, changed


def kubernetes_login(addr: str, auth_mount: str, role: str, jwt: str) -> tuple[str, int]:
    """Exchange a Kubernetes ServiceAccount JWT for an OpenBao token.

    Uses the kubernetes auth method at the given mount and role, the same auth
    path External Secrets uses. Returns the client token and its lease duration
    in seconds (0 means non-expiring).
    """
    client = hvac.Client(url=addr.rstrip("/"), timeout=_TIMEOUT)
    try:
        resp = client.auth.kubernetes.login(role=role, jwt=jwt, mount_point=auth_mount.strip("/"))
    except Exception as exc:  # noqa: BLE001 - surface any login failure uniformly
        raise OpenBaoError(f"openbao kubernetes login: {exc}") from exc

    auth = (resp or {}).get("auth") or {}
    token = auth.get("client_token") or ""
    if not token:
        raise OpenBaoError("openbao kubernetes login returned no client token")

    return token, int(auth.get("lease_duration") or 0)
