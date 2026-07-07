"""A tiny Harbor admin client: project + pull-robot provisioning.

Used by the Application engine so built images land in a project that exists
and app namespaces can pull from it with least privilege: the controller
ensures the project (private) and a project-scoped robot account that can
only pull. The robot's secret is returned exactly once by Harbor, so the
caller stores it in OpenBao at creation ("generate once") and delivers it to
app namespaces via an ExternalSecret. HTTP is the standard library ``urllib``
against Harbor's v2.0 REST API, authenticated as the admin push user.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request


class HarborError(RuntimeError):
    """A Harbor API call failed."""


def _api_request(base: str, auth: str, method: str, path: str, body: dict | None = None):
    """One basic-authenticated Harbor REST call; returns the decoded JSON body."""
    data = json.dumps(body).encode() if body is not None else None

    req = urllib.request.Request(f"{base}/api/v2.0{path}", data=data, method=method)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")

    if data is not None:
        req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req) as resp:  # noqa: S310 - operator-configured registry
        raw = resp.read()

        return json.loads(raw) if raw else None


def _basic_auth(username: str, password: str) -> str:
    return base64.b64encode(f"{username}:{password}".encode()).decode()


class HarborClient:
    """Minimal admin-authenticated Harbor REST client."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self._base = base_url.rstrip("/")
        self._auth = _basic_auth(username, password)

    def _request(self, method: str, path: str, body: dict | None = None):
        try:
            return _api_request(self._base, self._auth, method, path, body)
        except urllib.error.HTTPError as exc:
            raise HarborError(f"harbor {method} {path}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise HarborError(f"harbor {method} {path}: {exc.reason}") from exc

    def ensure_project(self, project: str) -> None:
        """Create the (private) project if it does not exist; idempotent."""
        try:
            self._request(
                "POST",
                "/projects",
                {"project_name": project, "metadata": {"public": "false"}},
            )
        except HarborError as exc:
            # 409 = already exists — exactly what "ensure" wants.
            if "HTTP 409" not in str(exc):
                raise

    def ensure_pull_robot(self, project: str, name: str = "pull") -> tuple[str, str]:
        """(Re)create the project's pull-only robot; returns (username, secret).

        Harbor only reveals a robot's secret at creation, so this is called when
        the stored credential is lost or absent: any existing robot of the same
        name is deleted first, then recreated fresh. The returned username is
        the full prefixed account name (e.g. ``robot$previews+pull``).
        """
        existing = self._request("GET", f"/projects/{project}/robots?page_size=100") or []

        for robot in existing:
            if (robot.get("name") or "").endswith(f"+{name}"):
                self._request("DELETE", f"/robots/{robot['id']}")

        created = self._request(
            "POST",
            "/robots",
            {
                "name": name,
                "description": "platform-controller: image pulls for app namespaces",
                "duration": -1,
                "level": "project",
                "permissions": [
                    {
                        "kind": "project",
                        "namespace": project,
                        "access": [{"resource": "repository", "action": "pull"}],
                    },
                ],
            },
        )

        username = (created or {}).get("name") or ""
        secret = (created or {}).get("secret") or ""

        if not username or not secret:
            raise HarborError("harbor robot creation returned no credentials")

        return username, secret
