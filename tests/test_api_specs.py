"""Tests for the API's intent -> CR spec builders (controller vocabulary)."""

from __future__ import annotations

from adomi_platform_api import specs


def test_client_spec():
    assert specs.client_spec(display_name="Acme", slug="acme", organization="adomi") == {
        "displayName": "Acme",
        "slug": "acme",
        "organizationRef": {"name": "adomi"},
    }


def test_workspace_spec_drops_none():
    assert specs.workspace_spec(client="acme", workspace_class="production") == {
        "clientRef": {"name": "acme"},
        "class": "production",
    }


def test_application_spec_attach_db_and_domain():
    spec = specs.application_spec(
        workspace="prod", type="odoo", database="erp-db", domain="acme-com"
    )
    assert spec["workspaceRef"] == {"name": "prod"}
    assert spec["databaseRef"] == {"name": "erp-db"}
    assert spec["domainRef"] == {"name": "acme-com"}
    assert "database" not in spec  # databaseRef wins over database_mode


def test_application_spec_database_mode_source_integrations():
    spec = specs.application_spec(
        workspace="dev",
        type="odoo",
        database_mode="cnpg",
        source={"repository": "erp-src", "ref": "main"},
        integrations=[{"type": "odoo-mailpit-smtp", "from": "mail"}],
    )
    assert spec["database"] == {"mode": "cnpg"}
    assert spec["source"] == {"repositoryRef": {"name": "erp-src"}, "ref": "main"}
    assert spec["integrations"] == [{"type": "odoo-mailpit-smtp", "fromRef": {"name": "mail"}}]


def test_domain_database_gitrepository_snapshot_specs():
    assert specs.domain_spec(fqdn="acme.example.com") == {
        "fqdn": "acme.example.com",
        "wildcard": True,
    }
    assert specs.database_spec(storage="20Gi", environment="prod") == {
        "engine": "postgres",
        "storage": "20Gi",
        "instances": 1,
        "environmentRef": {"name": "prod"},
    }
    assert specs.gitrepository_spec(url="https://x/erp", credentials_secret="erp-token") == {
        "url": "https://x/erp",
        "defaultBranch": "main",
        "credentialsSecretRef": {"name": "erp-token"},
    }
    assert specs.snapshot_spec(application="erp") == {"applicationRef": {"name": "erp"}}
