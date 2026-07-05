"""Authentik admin client: who can reach an application.

Identity is the one part of the platform that deliberately does NOT live in
git — group membership changes are imperative Authentik state (the same way
secret values are imperative OpenBao state). This client covers exactly what
the portal's per-app access management needs: list users, keep one access
group per app, and bind/unbind that group to the Authentik application so
membership actually ENFORCES who gets through.

No FastAPI import and an injectable ``session`` (anything exposing
``request(method, url, headers=, content=, timeout=)``) so it unit-tests
without a network — the same shape as the Forgejo writer.
"""

from __future__ import annotations

import json


class IdentityError(Exception):
    """An Authentik request failed (network or non-2xx response)."""


def _default_session(verify: bool, timeout: float):
    import httpx

    return httpx.Client(verify=verify, timeout=timeout)


class AuthentikAdmin:
    """Talks to Authentik's v3 API with an admin token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        session=None,
        timeout: float = 15.0,
        verify: bool = True,
    ):
        if not base_url:
            raise IdentityError("Authentik URL is not configured.")
        if not token:
            raise IdentityError("Authentik token is not configured.")

        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.verify = verify
        self._session = session

    @property
    def session(self):
        if self._session is None:
            self._session = _default_session(self.verify, self.timeout)

        return self._session

    # --- low-level HTTP ---------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v3/{path.lstrip('/')}"

    def _request(self, method: str, path: str, payload=None, params=None):
        url = self._url(path)

        if params:
            url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

        content = json.dumps(payload).encode("utf-8") if payload is not None else None

        try:
            return self.session.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                content=content,
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - surface network errors uniformly
            raise IdentityError(f"Authentik request failed: {exc}") from exc

    def _json(self, resp, what: str) -> dict:
        if resp.status_code not in (200, 201, 204):
            raise IdentityError(f"{what} failed: {resp.status_code} {resp.text}")
        try:
            return resp.json() if resp.status_code != 204 else {}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _user(u: dict) -> dict:
        return {
            "pk": u.get("pk"),
            "username": u.get("username") or "",
            "name": u.get("name") or "",
            "email": u.get("email") or "",
        }

    # --- users --------------------------------------------------------------
    def list_users(self, search: str = "", limit: int = 50) -> list[dict]:
        """Human accounts (service accounts excluded), optionally filtered."""
        params = {"page_size": int(limit), "ordering": "username"}
        if search:
            params["search"] = search

        data = self._json(self._request("GET", "core/users/", params=params), "Listing users")

        return [
            self._user(u)
            for u in data.get("results") or []
            if u.get("type") in (None, "internal", "external") and u.get("is_active", True)
        ]

    # --- groups ---------------------------------------------------------------
    def find_group(self, name: str) -> dict | None:
        """The group (with members) or None. Exact name match."""
        data = self._json(
            self._request("GET", "core/groups/", params={"name": name, "include_users": "true"}),
            f"Reading group {name!r}",
        )
        for group in data.get("results") or []:
            if group.get("name") == name:
                return group

        return None

    def ensure_group(self, name: str) -> dict:
        existing = self.find_group(name)
        if existing:
            return existing

        return self._json(
            self._request("POST", "core/groups/", {"name": name}),
            f"Creating group {name!r}",
        )

    def group_members(self, group: dict) -> list[dict]:
        return [self._user(u) for u in group.get("users_obj") or []]

    def add_member(self, group_pk: str, user_pk: int) -> None:
        self._json(
            self._request("POST", f"core/groups/{group_pk}/add_user/", {"pk": int(user_pk)}),
            "Adding the user to the group",
        )

    def remove_member(self, group_pk: str, user_pk: int) -> None:
        self._json(
            self._request("POST", f"core/groups/{group_pk}/remove_user/", {"pk": int(user_pk)}),
            "Removing the user from the group",
        )

    # --- application access bindings -------------------------------------------
    def application_by_slug(self, slug: str) -> dict | None:
        data = self._json(
            self._request("GET", "core/applications/", params={"slug": slug}),
            f"Reading application {slug!r}",
        )
        for app in data.get("results") or []:
            if app.get("slug") == slug:
                return app

        return None

    def group_bindings(self, target_pk: str) -> list[dict]:
        """Group policy-bindings on an Authentik object (the access gate)."""
        data = self._json(
            self._request("GET", "policies/bindings/", params={"target": target_pk}),
            "Reading access bindings",
        )

        return [b for b in data.get("results") or [] if b.get("group")]

    def ensure_binding(self, target_pk: str, group_pk: str) -> None:
        """Bind the group to the application (members-only access), once."""
        if any(b.get("group") == group_pk for b in self.group_bindings(target_pk)):
            return
        self._json(
            self._request(
                "POST",
                "policies/bindings/",
                {"target": target_pk, "group": group_pk, "order": 0, "enabled": True},
            ),
            "Binding the access group",
        )

    def remove_binding(self, target_pk: str, group_pk: str) -> None:
        """Drop the group's binding (the app is open to every signed-in user again)."""
        for binding in self.group_bindings(target_pk):
            if binding.get("group") == group_pk:
                resp = self._request("DELETE", f"policies/bindings/{binding['pk']}/")
                if resp.status_code not in (204, 404):
                    raise IdentityError(
                        f"Removing the access binding failed: {resp.status_code} {resp.text}"
                    )
