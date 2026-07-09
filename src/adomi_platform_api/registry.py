"""Harbor registry read client: which built images a client has.

The platform builds every from-source app into one Harbor project, with
repositories named ``<client>-<app>`` (resolve.built_image_ref in the
controller). This client covers exactly what the portal's Images section
needs: list a client's repositories and their tagged artifacts. Read-only —
image creation belongs to the build pipeline, deletion/retention to Harbor.

No FastAPI import and an injectable ``session`` (anything exposing
``request(method, url, headers=, timeout=)``) so it unit-tests without a
network — the same shape as the Authentik admin client.
"""

from __future__ import annotations

import base64


class RegistryError(Exception):
    """A Harbor request failed (network or non-2xx response)."""


def _default_session(verify: bool, timeout: float):
    import httpx

    return httpx.Client(verify=verify, timeout=timeout)


class HarborRegistry:
    """Talks to Harbor's v2.0 API with the admin credential (read-only use)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        session=None,
        timeout: float = 15.0,
        verify: bool = True,
    ):
        if not base_url:
            raise RegistryError("Harbor URL is not configured.")
        if not username or not password:
            raise RegistryError("Harbor credentials are not configured.")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify = verify
        self._auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._session = session

    @property
    def session(self):
        if self._session is None:
            self._session = _default_session(self.verify, self.timeout)

        return self._session

    # --- low-level HTTP ---------------------------------------------------------
    def _get(self, path: str, params: dict | None = None):
        url = f"{self.base_url}/api/v2.0/{path.lstrip('/')}"

        if params:
            url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

        try:
            resp = self.session.request(
                "GET",
                url,
                headers={
                    "Authorization": f"Basic {self._auth}",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - surface network errors uniformly
            raise RegistryError(f"Harbor request failed: {exc}") from exc

        if resp.status_code != 200:
            raise RegistryError(f"Harbor GET {path} failed: {resp.status_code} {resp.text}")

        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return []

    # --- images -------------------------------------------------------------
    def list_repositories(self, project: str, prefix: str) -> list[str]:
        """Repository names (without the project) starting with ``prefix``.

        Harbor's ``q=name=~`` match is a substring filter, so the prefix is
        re-checked here; repository names come back project-qualified
        (``previews/acme-erp``) and are returned bare (``acme-erp``).
        """
        repos = (
            self._get(
                f"projects/{project}/repositories",
                {"page_size": 100, "q": f"name%3D~{prefix}"},
            )
            or []
        )

        names = []
        for repo in repos:
            name = (repo.get("name") or "").removeprefix(f"{project}/")

            if name.startswith(prefix):
                names.append(name)

        return sorted(names)

    def list_artifacts(self, project: str, repository: str) -> list[dict]:
        """Tagged artifacts of one repository, newest push first (raw Harbor dicts)."""
        return (
            self._get(
                f"projects/{project}/repositories/{repository}/artifacts",
                {"page_size": 100, "with_tag": "true", "sort": "-push_time"},
            )
            or []
        )
