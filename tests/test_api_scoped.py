"""Scoped Variables + Secrets endpoints: git RMW for variables, OpenBao for secrets."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from adomi_platform_api import app as app_module  # noqa: E402
from adomi_platform_api.config import get_settings  # noqa: E402
from adomi_platform_api.deps import get_secrets_store, get_service  # noqa: E402
from adomi_platform_api.git import Readiness  # noqa: E402
from adomi_platform_api.service import ClientService  # noqa: E402

AUTH = {"Authorization": "Bearer secret"}


class _RepoWriter:
    """In-memory git backend: real read-modify-write against stored files."""

    def __init__(self):
        self.files: dict[tuple[str, str], str] = {}

    def read_manifest(self, repo, path):
        return self.files.get((repo, path))

    def apply_manifest(self, repo, path, content, message, mode="commit"):
        self.files[(repo, path)] = content
        return {"committed": True, "branch": "main"}

    def delete_manifest(self, repo, path, message, mode="commit"):
        self.files.pop((repo, path), None)
        return {"deleted": True, "branch": "main"}

    def check_ready(self):
        return Readiness.up()


class _FakeSecrets:
    """In-memory scoped-secret store with the same map-per-path semantics."""

    def __init__(self):
        self.paths: dict[str, dict[str, str]] = {}

    def names(self, path):
        return sorted(self.paths.get(path, {}).keys())

    def set(self, path, name, value):
        self.paths.setdefault(path, {})[name] = value

    def remove(self, path, name):
        data = self.paths.get(path)
        if not data or name not in data:
            return False
        data.pop(name)
        if not data:
            self.paths.pop(path)
        return True


@pytest.fixture
def api(monkeypatch):
    writer = _RepoWriter()
    secrets = _FakeSecrets()

    get_settings.cache_clear()
    monkeypatch.setenv("ADOMI_API_AUTH_TOKEN", "secret")

    app_module.app.dependency_overrides[get_service] = lambda: ClientService(
        writer, namespace_prefix="adomi-client-", managed_by="test", git_mode="commit"
    )
    app_module.app.dependency_overrides[get_secrets_store] = lambda: secrets

    client = TestClient(app_module.app)
    # Seed a committed Client CR the variables endpoints operate on.
    client.put("/v1/clients/acme", json={"display_name": "Acme"}, headers=AUTH)
    yield client, writer, secrets, get_settings()
    app_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_variable_roundtrip_on_client(api):
    client, writer, _, _ = api

    r = client.put("/v1/clients/acme/variables/TZ", json={"value": "UTC"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert "TZ" in writer.files[("acme", "clients/acme.yaml")]

    r = client.get("/v1/clients/acme/variables", headers=AUTH)
    assert r.json() == [{"name": "TZ", "value": "UTC"}]

    # Overwrite, then remove; the CR loses spec.variables when the last one goes.
    client.put("/v1/clients/acme/variables/TZ", json={"value": "EST"}, headers=AUTH)
    r = client.get("/v1/clients/acme/variables", headers=AUTH)
    assert r.json() == [{"name": "TZ", "value": "EST"}]

    r = client.delete("/v1/clients/acme/variables/TZ", headers=AUTH)
    assert r.status_code == 200
    assert "variables" not in writer.files[("acme", "clients/acme.yaml")]


def test_resource_upsert_preserves_committed_variables(api):
    """A plain resource PUT must not clobber the variables RMW'd onto the CR."""
    client, writer, _, _ = api

    r = client.put("/v1/clients/acme/variables/TZ", json={"value": "UTC"}, headers=AUTH)
    assert r.status_code == 200, r.text

    # Re-upsert the client itself (e.g. renaming it in the portal).
    r = client.put("/v1/clients/acme", json={"display_name": "Acme Corp"}, headers=AUTH)
    assert r.status_code == 200, r.text

    r = client.get("/v1/clients/acme/variables", headers=AUTH)
    assert r.json() == [{"name": "TZ", "value": "UTC"}]


def test_variable_on_missing_cr_is_404(api):
    client, _, _, _ = api
    r = client.put(
        "/v1/clients/acme/environments/prod/variables/TZ", json={"value": "UTC"}, headers=AUTH
    )
    assert r.status_code == 404


def test_secret_values_never_leave_the_store(api):
    client, writer, secrets, settings = api

    r = client.put("/v1/clients/acme/secrets/API_KEY", json={"value": "hunter2"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"stored": True, "name": "API_KEY"}

    path = f"{settings.scoped_secrets_prefix}/clients/acme"
    assert secrets.paths[path] == {"API_KEY": "hunter2"}

    # GET returns names only, and nothing secret ever hit git.
    r = client.get("/v1/clients/acme/secrets", headers=AUTH)
    assert r.json() == ["API_KEY"]
    assert all("hunter2" not in content for content in writer.files.values())

    r = client.delete("/v1/clients/acme/secrets/API_KEY", headers=AUTH)
    assert r.json() == {"deleted": True, "name": "API_KEY"}
    assert path not in secrets.paths  # last key removes the path (revokes delivery)


def test_secret_paths_match_controller_scheme(api):
    client, _, secrets, settings = api
    client.put(
        "/v1/clients/acme/environments/prod/applications/erp/secrets/DB",
        json={"value": "x"},
        headers=AUTH,
    )
    assert (
        f"{settings.scoped_secrets_prefix}/clients/acme/environments/prod/applications/erp"
        in secrets.paths
    )


def test_org_secrets(api):
    client, _, secrets, settings = api
    client.put("/v1/organizations/adomi-org/secrets/SMTP", json={"value": "s"}, headers=AUTH)
    assert secrets.paths[f"{settings.scoped_secrets_prefix}/org/adomi-org"] == {"SMTP": "s"}


def test_bad_secret_name_rejected(api):
    client, _, _, _ = api
    r = client.put("/v1/clients/acme/secrets/bad name!", json={"value": "x"}, headers=AUTH)
    assert r.status_code == 400
