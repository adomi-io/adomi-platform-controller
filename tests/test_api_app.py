"""Route-level tests (FastAPI TestClient) with fake git writer + cluster reader."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from adomi_platform_api import app as app_module  # noqa: E402
from adomi_platform_api.config import get_settings  # noqa: E402
from adomi_platform_api.deps import check_backend_ready, get_reader, get_service  # noqa: E402
from adomi_platform_api.git import Readiness  # noqa: E402
from adomi_platform_api.service import ClientService  # noqa: E402

AUTH = {"Authorization": "Bearer secret"}


class _FakeWriter:
    def __init__(self):
        self.applied, self.deleted = [], []

    def read_manifest(self, repo, path):
        return None  # nothing committed yet: upserts always write fresh

    def apply_manifest(self, repo, path, content, message, mode="commit"):
        self.applied.append({"repo": repo, "path": path, "content": content})
        return {"committed": True, "branch": "main"}

    def delete_manifest(self, repo, path, message, mode="commit"):
        self.deleted.append({"repo": repo, "path": path})
        return {"deleted": True, "branch": "main"}

    def check_ready(self):
        return Readiness.up()


class _FakeReader:
    def __init__(self):
        self.objs: dict[tuple[str, str], dict] = {}

    def add(self, plural, name, namespace, **status):
        self.objs[(plural, name)] = {
            "kind": plural[:-1].capitalize(),
            "metadata": {"name": name, "namespace": namespace},
            "spec": status.pop("spec", {}),
            "status": status,
        }

    def get(self, plural, namespace, name):
        return self.objs.get((plural, name))

    def list(self, plural, namespace=None, label_selector=""):
        return [o for (p, _), o in self.objs.items() if p == plural]


@pytest.fixture
def ctx(monkeypatch):
    writer = _FakeWriter()
    reader = _FakeReader()
    service = ClientService(
        writer, namespace_prefix="adomi-client-", managed_by="test", git_mode="commit"
    )
    app_module.app.dependency_overrides[get_service] = lambda: service
    app_module.app.dependency_overrides[get_reader] = lambda: reader
    app_module.app.dependency_overrides[check_backend_ready] = lambda: writer.check_ready()
    get_settings.cache_clear()
    monkeypatch.setenv("ADOMI_API_AUTH_TOKEN", "secret")
    yield writer, reader
    app_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def _c():
    return TestClient(app_module.app)


def test_requires_bearer(ctx):
    assert _c().put("/v1/clients/acme", json={"display_name": "Acme"}).status_code == 401


def test_put_client(ctx):
    writer, _ = ctx
    r = _c().put("/v1/clients/acme", json={"display_name": "Acme"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert writer.applied[0]["path"] == "clients/acme.yaml"
    assert "kind: Client" in writer.applied[0]["content"]
    assert "namespace: adomi-client-acme" in writer.applied[0]["content"]


def test_put_environment_class_alias_and_application(ctx):
    writer, _ = ctx
    c = _c()
    assert (
        c.put(
            "/v1/clients/acme/environments/prod", json={"class": "production"}, headers=AUTH
        ).status_code
        == 200
    )
    assert "kind: Environment" in writer.applied[-1]["content"]
    r = c.put(
        "/v1/clients/acme/environments/prod/applications/erp",
        json={
            "type": "odoo",
            "replicas": 2,
            "host": "erp.acme.example.com",
            "domain": "acme-example-com",
            "databases": [
                {"name": "erp", "server": "acme-prod-db", "credentials": {"secret": "erp-db"}}
            ],
        },
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    body = writer.applied[-1]["content"]
    assert "kind: Application" in body and "databases" in body
    assert "replicas: 2" in body and "erp.acme.example.com" in body
    assert "domainRef" in body and "acme-example-com" in body
    assert writer.applied[-1]["path"] == "applications/erp.yaml"


def test_invalid_name_is_400(ctx):
    assert (
        _c().put("/v1/clients/Bad_Name", json={"display_name": "x"}, headers=AUTH).status_code
        == 400
    )


def test_get_status(ctx):
    _, reader = ctx
    reader.add(
        "applications",
        "erp",
        "adomi-client-acme",
        phase="Deployed",
        url="https://erp.example.com",
        conditions=[{"type": "Ready", "status": "True", "message": "ok"}],
        spec={"environmentRef": {"name": "prod"}},
    )
    r = _c().get("/v1/clients/acme/environments/prod/applications/erp", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready"] == "True" and body["phase"] == "Deployed"
    assert body["url"] == "https://erp.example.com"


def test_get_status_404(ctx):
    assert _c().get("/v1/clients/acme/domains/missing", headers=AUTH).status_code == 404


def test_list_applications_filtered_by_environment(ctx):
    _, reader = ctx
    reader.add(
        "applications", "erp", "adomi-client-acme", spec={"environmentRef": {"name": "prod"}}
    )
    reader.add(
        "applications", "mail", "adomi-client-acme", spec={"environmentRef": {"name": "dev"}}
    )
    r = _c().get("/v1/clients/acme/environments/prod/applications", headers=AUTH)
    assert r.status_code == 200
    names = [a["name"] for a in r.json()]
    assert names == ["erp"]


def test_delete_application(ctx):
    writer, _ = ctx
    r = _c().delete("/v1/clients/acme/environments/prod/applications/erp", headers=AUTH)
    assert r.status_code == 200
    assert writer.deleted[0]["path"] == "applications/erp.yaml"


def test_readyz_and_healthz(ctx):
    c = _c()
    assert c.get("/readyz").status_code == 200
    assert c.get("/healthz").json()["status"] == "ok"
