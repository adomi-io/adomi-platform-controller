"""Scoped-secret storage: the API's OpenBao KV v2 access.

Secret VALUES never touch git — the portal (or any client of this API) sends
them here and they land at per-scope KV paths (see the controller's
``resolve.scoped_secret_paths``); the controller delivers the merged result to
workloads via an ExternalSecret. This client is deliberately tiny: one KV map
per scope path, read-modify-write per key.

Auth is either a static token (dev/tests) or the Kubernetes auth method with
the API's own ServiceAccount JWT — the same flow the controller and External
Secrets use.
"""

from __future__ import annotations

import hvac
from hvac.exceptions import Forbidden, InvalidPath, Unauthorized

_TIMEOUT = 15  # seconds


class SecretsError(RuntimeError):
    """An OpenBao request failed."""


class ScopedSecretsStore:
    """Reads and writes scoped-secret maps in one OpenBao KV v2 mount."""

    def __init__(
        self,
        addr: str,
        mount: str,
        *,
        token: str = "",
        auth_mount: str = "kubernetes",
        role: str = "",
        jwt_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token",
    ):
        if not addr:
            raise SecretsError("OpenBao address is not configured.")
        self._mount = mount.strip("/")
        self._static_token = token
        self._auth_mount = auth_mount.strip("/")
        self._role = role
        self._jwt_path = jwt_path
        self._client = hvac.Client(url=addr.rstrip("/"), timeout=_TIMEOUT)

    def _login(self) -> str:
        if self._static_token:
            return self._static_token
        if not self._role:
            raise SecretsError("OpenBao role is not configured (and no static token).")
        with open(self._jwt_path, encoding="utf-8") as fh:
            jwt = fh.read().strip()
        try:
            resp = self._client.auth.kubernetes.login(
                role=self._role, jwt=jwt, mount_point=self._auth_mount
            )
        except Exception as exc:  # noqa: BLE001 - surface login failures uniformly
            raise SecretsError(f"openbao kubernetes login: {exc}") from exc
        token = ((resp or {}).get("auth") or {}).get("client_token") or ""
        if not token:
            raise SecretsError("openbao kubernetes login returned no token")
        return token

    def _call(self, op):
        for attempt in range(2):
            self._client.token = (
                self._login() if attempt or not self._client.token else (self._client.token)
            )
            try:
                return op()
            except InvalidPath:
                return None
            except (Forbidden, Unauthorized):
                if attempt == 0:
                    self._client.token = None
                    continue
                raise SecretsError("openbao: access denied") from None
        raise SecretsError("openbao: failed after token refresh")

    def _read(self, path: str) -> dict | None:
        return self._call(
            lambda: self._client.secrets.kv.v2.read_secret_version(
                path=path.strip("/"), mount_point=self._mount, raise_on_deleted_version=True
            )["data"]["data"]
        )

    def names(self, path: str) -> list[str]:
        """The secret NAMES stored at a scope path (never the values)."""
        return sorted((self._read(path) or {}).keys())

    def set(self, path: str, name: str, value: str) -> None:
        """Set one secret in the scope's map (read-modify-write)."""
        data = dict(self._read(path) or {})
        data[name] = value
        self._call(
            lambda: self._client.secrets.kv.v2.create_or_update_secret(
                path=path.strip("/"), secret=data, mount_point=self._mount
            )
        )

    def remove(self, path: str, name: str) -> bool:
        """Remove one secret; deletes the whole path when it was the last key.

        Returns False when the name wasn't present (idempotent delete).
        """
        data = dict(self._read(path) or {})
        if name not in data:
            return False
        data.pop(name)
        if data:
            self._call(
                lambda: self._client.secrets.kv.v2.create_or_update_secret(
                    path=path.strip("/"), secret=data, mount_point=self._mount
                )
            )
        else:
            # An empty map would still be "a path that exists" to the controller's
            # delivery check; remove it entirely so access revokes cleanly.
            self._call(
                lambda: self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                    path=path.strip("/"), mount_point=self._mount
                )
            )
        return True
