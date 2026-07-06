"""Tests for the Authentik identity client + the per-app access routes."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from adomi_platform_api import app as app_module  # noqa: E402
from adomi_platform_api.config import get_settings  # noqa: E402
from adomi_platform_api.deps import get_identity, get_reader  # noqa: E402
from adomi_platform_api.identity import AuthentikAdmin, IdentityError  # noqa: E402

AUTH = {"Authorization": "Bearer secret"}


# --- the httpx-shaped stub (same pattern as the Forgejo tests) -------------------
class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _StubSession:
    def __init__(self, rules):
        self.calls = []
        self.rules = rules

    def request(self, method, url, headers=None, content=None, timeout=None):
        body = json.loads(content.decode("utf-8")) if content else None
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        best = None
        for (m, frag), resp in self.rules:
            if m == method and frag in url and (best is None or len(frag) > len(best[0])):
                best = (frag, resp)
        return best[1] if best else _Resp(500, text=f"no rule for {method} {url}")


def _admin(session):
    return AuthentikAdmin("https://auth.example.com", "tok", session=session)


def test_list_users_filters_service_accounts():
    session = _StubSession(
        [
            (
                ("GET", "core/users/"),
                _Resp(
                    200,
                    {
                        "results": [
                            {"pk": 1, "username": "kyle", "name": "Kyle", "type": "internal"},
                            {"pk": 2, "username": "cory", "name": "Cory", "type": "external"},
                            {
                                "pk": 3,
                                "username": "ak-outpost",
                                "type": "internal_service_account",
                            },
                            {"pk": 4, "username": "gone", "type": "internal", "is_active": False},
                        ]
                    },
                ),
            ),
        ]
    )
    users = _admin(session).list_users(search="k")
    assert [u["pk"] for u in users] == [1, 2]
    assert "search=k" in session.calls[0]["url"]
    assert session.calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_ensure_group_finds_exact_match_or_creates():
    found = _StubSession(
        [
            (
                ("GET", "core/groups/"),
                _Resp(200, {"results": [{"pk": "g1", "name": "app-access-x", "users_obj": []}]}),
            ),
        ]
    )
    assert _admin(found).ensure_group("app-access-x")["pk"] == "g1"
    assert not [c for c in found.calls if c["method"] == "POST"]

    created = _StubSession(
        [
            (("GET", "core/groups/"), _Resp(200, {"results": []})),
            (("POST", "core/groups/"), _Resp(201, {"pk": "g2", "name": "app-access-x"})),
        ]
    )
    assert _admin(created).ensure_group("app-access-x")["pk"] == "g2"


def test_ensure_binding_is_idempotent():
    bound = _StubSession(
        [
            (
                ("GET", "policies/bindings/"),
                _Resp(200, {"results": [{"pk": "b1", "group": "g1"}]}),
            ),
        ]
    )
    _admin(bound).ensure_binding("app-pk", "g1")
    assert not [c for c in bound.calls if c["method"] == "POST"]

    unbound = _StubSession(
        [
            (("GET", "policies/bindings/"), _Resp(200, {"results": []})),
            (("POST", "policies/bindings/"), _Resp(201, {"pk": "b2"})),
        ]
    )
    _admin(unbound).ensure_binding("app-pk", "g1")
    post = [c for c in unbound.calls if c["method"] == "POST"][0]
    assert post["body"] == {"target": "app-pk", "group": "g1", "order": 0, "enabled": True}


def test_remove_binding_deletes_only_our_group():
    session = _StubSession(
        [
            (
                ("GET", "policies/bindings/"),
                _Resp(
                    200,
                    {"results": [{"pk": "b1", "group": "g1"}, {"pk": "b2", "group": "other"}]},
                ),
            ),
            (("DELETE", "policies/bindings/b1/"), _Resp(204)),
        ]
    )
    _admin(session).remove_binding("app-pk", "g1")
    deletes = [c for c in session.calls if c["method"] == "DELETE"]
    assert len(deletes) == 1 and "bindings/b1/" in deletes[0]["url"]


def test_application_lookup_uses_superuser_full_list():
    """Regression: a gated app disappears from the default (access-filtered)
    applications list, breaking restricted-detection and last-revoke unbinding."""
    session = _StubSession(
        [
            (
                ("GET", "core/applications/"),
                _Resp(200, {"results": [{"pk": "app-1", "slug": "erp-sso"}]}),
            ),
        ]
    )
    assert _admin(session).application_by_slug("erp-sso")["pk"] == "app-1"
    assert "superuser_full_list=true" in session.calls[0]["url"]


def test_missing_config_raises():
    with pytest.raises(IdentityError):
        AuthentikAdmin("", "tok", session=_StubSession([]))
    with pytest.raises(IdentityError):
        AuthentikAdmin("https://x", "", session=_StubSession([]))


# --- route level ------------------------------------------------------------------
class _FakeIdentity:
    """In-memory Authentik: groups, members, app bindings."""

    def __init__(self):
        self.users = [
            {"pk": 1, "username": "kyle", "name": "Kyle", "email": "k@example.com"},
            {"pk": 2, "username": "cory", "name": "Cory", "email": "c@example.com"},
        ]
        self.groups: dict[str, dict] = {}
        self.bindings: dict[str, set[str]] = {}  # app pk -> group pks
        self.apps = {"erp-sso": {"pk": "app-1", "slug": "erp-sso"}}

    def list_users(self, search=""):
        return [u for u in self.users if search.lower() in u["username"]] if search else self.users

    def find_group(self, name):
        return self.groups.get(name)

    def ensure_group(self, name):
        return self.groups.setdefault(name, {"pk": f"pk-{name}", "name": name, "users_obj": []})

    def group_members(self, group):
        return list(group.get("users_obj") or [])

    def add_member(self, group_pk, user_pk):
        group = next(g for g in self.groups.values() if g["pk"] == group_pk)
        user = next(u for u in self.users if u["pk"] == user_pk)
        if user not in group["users_obj"]:
            group["users_obj"].append(user)

    def remove_member(self, group_pk, user_pk):
        group = next(g for g in self.groups.values() if g["pk"] == group_pk)
        group["users_obj"] = [u for u in group["users_obj"] if u["pk"] != user_pk]

    def application_by_slug(self, slug):
        return self.apps.get(slug)

    def group_bindings(self, target_pk):
        return [{"pk": "b", "group": g} for g in self.bindings.get(target_pk, set())]

    def ensure_binding(self, target_pk, group_pk):
        self.bindings.setdefault(target_pk, set()).add(group_pk)

    def remove_binding(self, target_pk, group_pk):
        self.bindings.get(target_pk, set()).discard(group_pk)


class _FakeReader:
    def __init__(self, sso_items):
        self.sso_items = sso_items

    def list(self, plural, namespace=None, label_selector="", group=None):
        assert plural == "ssoapplications" and group == "identity.adomi.io"
        return self.sso_items


@pytest.fixture
def access_ctx(monkeypatch):
    identity = _FakeIdentity()
    reader = _FakeReader(
        [
            {
                "metadata": {"name": "web", "namespace": "acme-prod"},
                "status": {"slug": "erp-sso"},
            }
        ]
    )
    app_module.app.dependency_overrides[get_identity] = lambda: identity
    app_module.app.dependency_overrides[get_reader] = lambda: reader
    get_settings.cache_clear()
    monkeypatch.setenv("ADOMI_API_AUTH_TOKEN", "secret")
    yield identity
    app_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def _c():
    return TestClient(app_module.app)


def test_access_lifecycle(access_ctx):
    identity = access_ctx
    c = _c()
    base = "/v1/clients/acme/environments/prod/applications/erp/access"

    r = c.get("/v1/identity/users", headers=AUTH)
    assert r.status_code == 200 and [u["pk"] for u in r.json()] == [1, 2]

    # Fresh app: everyone with SSO.
    r = c.get(base, headers=AUTH)
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["available"] and state["mode"] == "everyone"
    assert state["group"] == "app-access-acme-prod-erp"

    # First grant flips to restricted and gates the Authentik app.
    r = c.put(f"{base}/1", headers=AUTH)
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["mode"] == "restricted"
    assert [u["pk"] for u in state["users"]] == [1]
    assert identity.bindings["app-1"] == {"pk-app-access-acme-prod-erp"}

    # Second user joins the same group.
    state = c.put(f"{base}/2", headers=AUTH).json()
    assert [u["pk"] for u in state["users"]] == [1, 2]

    # Revoking down to zero drops the binding: open to everyone again.
    c.delete(f"{base}/1", headers=AUTH)
    state = c.delete(f"{base}/2", headers=AUTH).json()
    assert state["mode"] == "everyone" and state["users"] == []
    assert identity.bindings["app-1"] == set()


def test_access_without_sso_is_unavailable(access_ctx):
    identity = access_ctx
    app_module.app.dependency_overrides[get_reader] = lambda: _FakeReader([])
    c = _c()
    base = "/v1/clients/acme/environments/prod/applications/bare/access"

    state = c.get(base, headers=AUTH).json()
    assert state["available"] is False and state["reason"] == "no_sso"

    assert c.put(f"{base}/1", headers=AUTH).status_code == 409
    assert identity.groups == {}
