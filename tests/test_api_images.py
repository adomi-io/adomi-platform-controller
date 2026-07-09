"""Tests for the Harbor registry client + the client images route."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from adomi_platform_api import app as app_module  # noqa: E402
from adomi_platform_api.config import get_settings  # noqa: E402
from adomi_platform_api.deps import get_registry  # noqa: E402
from adomi_platform_api.registry import HarborRegistry, RegistryError  # noqa: E402

AUTH = {"Authorization": "Bearer secret"}


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
        self.calls.append({"method": method, "url": url, "headers": headers})
        for (m, frag), resp in self.rules:
            if m == method and frag in url:
                return resp
        return _Resp(500, text=f"no rule for {method} {url}")


def _registry(session):
    return HarborRegistry("http://harbor-core.harbor.svc", "admin", "pw", session=session)


REPOS = [
    {"name": "previews/acme-erp", "artifact_count": 2},
    {"name": "previews/other-acme-site", "artifact_count": 1},  # substring match, not a prefix
]

ARTIFACTS = [
    {
        "digest": "sha256:abc",
        "size": 1024,
        "push_time": "2026-07-07T20:26:00.000Z",
        "tags": [{"name": "master"}],
    },
    {"digest": "sha256:old", "size": 512, "push_time": "2026-07-01T00:00:00.000Z", "tags": None},
]


def test_list_repositories_enforces_prefix_and_strips_project():
    session = _StubSession([(("GET", "/projects/previews/repositories"), _Resp(200, REPOS))])
    names = _registry(session).list_repositories("previews", "acme-")

    # Harbor's q= match is substring; other-acme-site must not leak into acme.
    assert names == ["acme-erp"]
    assert session.calls[0]["headers"]["Authorization"].startswith("Basic ")


def test_registry_raises_on_non_200():
    session = _StubSession([(("GET", "/projects"), _Resp(401, text="unauthorized"))])

    with pytest.raises(RegistryError, match="401"):
        _registry(session).list_repositories("previews", "acme-")


@pytest.fixture
def images_ctx(monkeypatch):
    session = _StubSession(
        [
            (
                ("GET", "/projects/previews/repositories?"),
                _Resp(200, [{"name": "previews/acme-erp"}]),
            ),
            (("GET", "/repositories/acme-erp/artifacts"), _Resp(200, ARTIFACTS)),
        ]
    )
    app_module.app.dependency_overrides[get_registry] = lambda: _registry(session)
    get_settings.cache_clear()
    monkeypatch.setenv("ADOMI_API_AUTH_TOKEN", "secret")
    monkeypatch.setenv("ADOMI_API_HARBOR_HOST", "harbor.example.com")
    yield session
    app_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_list_images_route(images_ctx):
    r = TestClient(app_module.app).get("/v1/clients/acme/images", headers=AUTH)

    assert r.status_code == 200, r.text
    images = r.json()
    # The untagged artifact (GC leftover) is dropped.
    assert len(images) == 1
    img = images[0]
    assert img["repository"] == "acme-erp"
    assert img["application"] == "erp"
    assert img["image"] == "harbor.example.com/previews/acme-erp:master"
    assert img["tags"] == ["master"]
    assert img["size_bytes"] == 1024
    assert img["pushed_at"].startswith("2026-07-07")
