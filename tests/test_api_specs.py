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


def test_application_spec_explicit_databases_env_ingress():
    spec = specs.application_spec(
        workspace="prod",
        type="odoo",
        display_name="ERP",
        databases=[
            {"name": "erp", "server": "acme-prod-db", "credentials": {"secret": "odoo-erp-db"}}
        ],
        env=[{"name": "ODOO_DB_HOST", "value": "erp-rw.acme-prod.svc.cluster.local"}],
        replicas=2,
        host="erp.acme.example.com",
        values={"odoo": {"workers": 4}},
    )
    assert spec["workspaceRef"] == {"name": "prod"}
    assert spec["displayName"] == "ERP"
    assert spec["databases"][0]["server"] == "acme-prod-db"
    assert spec["databases"][0]["credentials"]["secret"] == "odoo-erp-db"
    assert spec["env"][0]["name"] == "ODOO_DB_HOST"
    assert spec["replicas"] == 2
    assert spec["ingress"] == {"host": "erp.acme.example.com"}
    assert spec["values"] == {"odoo": {"workers": 4}}
    assert "domainRef" not in spec  # dropped: the Application CRD has no domainRef


def test_application_spec_sso_and_source():
    spec = specs.application_spec(
        workspace="dev",
        type="odoo",
        sso=[{"name": "web", "protocol": "oauth2", "credentials": {"secret": "odoo-oidc"}}],
        source={"repository": "erp-src", "ref": "main"},
    )
    assert spec["sso"][0]["credentials"]["secret"] == "odoo-oidc"
    assert spec["source"] == {"repositoryRef": {"name": "erp-src"}, "ref": "main"}


def test_application_spec_minimal():
    spec = specs.application_spec(workspace="dev", type="mailpit")
    assert spec == {"workspaceRef": {"name": "dev"}, "type": "mailpit"}


def test_domain_database_gitrepository_snapshot_specs():
    assert specs.domain_spec(fqdn="acme.example.com") == {
        "fqdn": "acme.example.com",
        "wildcard": True,
    }
    assert specs.gitrepository_spec(url="https://x/erp", credentials_secret="erp-token") == {
        "url": "https://x/erp",
        "defaultBranch": "main",
        "credentialsSecretRef": {"name": "erp-token"},
    }
    assert specs.snapshot_spec(application="erp") == {"applicationRef": {"name": "erp"}}


def test_databaseserver_spec_cnpg():
    assert specs.databaseserver_spec(storage="20Gi", environment="prod") == {
        "engine": "postgres",
        "mode": "cnpg",
        "cnpg": {"storage": "20Gi", "instances": 1},
        "environmentRef": {"name": "prod"},
    }


def test_databaseserver_spec_external():
    assert specs.databaseserver_spec(
        mode="external",
        host="db.example.com",
        port=5433,
        admin_user="postgres",
        admin_openbao_path="databases/acme-rds-admin",
        ssl_mode="require",
    ) == {
        "engine": "postgres",
        "mode": "external",
        "external": {"host": "db.example.com", "port": 5433, "sslMode": "require"},
        "admin": {"user": "postgres", "openbaoPath": "databases/acme-rds-admin"},
    }


def test_database_spec():
    assert specs.database_spec(
        server="acme-prod-server",
        database_name="acme_app_odoo_production",
        user="acme_app_odoo_production_user",
        environment="production",
    ) == {
        "serverRef": {"name": "acme-prod-server"},
        "databaseName": "acme_app_odoo_production",
        "user": "acme_app_odoo_production_user",
        "environmentRef": {"name": "production"},
    }
