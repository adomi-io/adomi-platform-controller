"""Tests for the app-type adapters (pure value mappers)."""

from __future__ import annotations

from adomi_platform_controller.apptypes import base, registry
from adomi_platform_controller.resolve import DbConnection

MIDDLEWARE = "authentik-authentik@kubernetescrd"


def _db() -> DbConnection:
    return DbConnection(
        host="odoo-db-rw.acme-production.svc.cluster.local",
        port=5432,
        name="app",
        user="app",
        password_secret_namespace="acme-production",
        password_secret_name="odoo-db-app",
        password_secret_key="password",
    )


def test_image_block():
    assert base.image_block(
        base.Ctx("a", "n", "h", "u", "traefik", image="ghcr.io/x/odoo:19.0")
    ) == {
        "repository": "ghcr.io/x/odoo",
        "tag": "19.0",
    }
    # No tag.
    assert base.image_block(base.Ctx("a", "n", "h", "u", "traefik", image="ghcr.io/x/odoo")) == {
        "repository": "ghcr.io/x/odoo"
    }
    # Registry port, no tag.
    assert base.image_block(base.Ctx("a", "n", "h", "u", "traefik", image="reg:5000/x")) == {
        "repository": "reg:5000/x"
    }


def test_odoo_adapter():
    ctx = base.Ctx(
        app_name="odoo",
        namespace="acme-production",
        host="odoo.x",
        url="https://odoo.x",
        ingress_class_name="traefik",
        longpolling=True,
        image="ghcr.io/adomi-io/odoo:19.0",
        db=_db(),
        sso_protocol="proxy",
        forward_auth_middleware=MIDDLEWARE,
        odoo={"workers": 2, "addons": {"initModules": "base"}},
    )
    v = registry.get("odoo").helm_values(ctx)
    assert v["postgresql"] == {"enabled": False}
    assert v["database"]["existingSecret"] == "odoo-db-app"
    assert v["image"]["tag"] == "19.0"
    assert v["ingress"]["longpolling"] == {"enabled": True}
    assert v["ingress"]["annotations"][base.TRAEFIK_MIDDLEWARE_ANNOTATION] == MIDDLEWARE
    assert v["odoo"]["workers"] == 2
    assert v["odoo"]["initModules"] == "base"

    conn = registry.get("odoo").connection(ctx)
    assert conn["url"] == "https://odoo.x"
    assert conn["db"]["host"] == _db().host
    assert conn["db"]["passwordSecret"] == "odoo-db-app"


def test_mailpit_adapter():
    ctx = base.Ctx(
        "mailpit",
        "acme-production",
        "mail.x",
        "https://mail.x",
        "traefik",
        sso_protocol="proxy",
        forward_auth_middleware=MIDDLEWARE,
    )
    v = registry.get("mailpit").helm_values(ctx)
    assert v["ingress"]["enabled"] is True
    assert "database" not in v
    conn = registry.get("mailpit").connection(ctx)
    assert conn["smtp"]["host"] == "mailpit.acme-production.svc.cluster.local"
    assert conn["smtp"]["port"] == 1025


def test_superset_adapter():
    ctx = base.Ctx(
        "superset",
        "acme-production",
        "bi.x",
        "https://bi.x",
        "traefik",
        db=_db(),
        sso_protocol="oauth2",
        sso_secret="superset-oidc",
    )
    v = registry.get("superset").helm_values(ctx)
    assert v["postgresql"] == {"enabled": False}
    assert v["supersetNode"]["connections"]["db_host"] == _db().host
    names = [e["name"] for e in v["extraEnvRaw"]]
    assert "DB_PASS" in names and "OIDC_CLIENT_ID" in names
    assert v["ingress"]["hosts"] == ["bi.x"]


def test_generic_adapter():
    ctx = base.Ctx("uptime", "acme-production", "up.x", "https://up.x", "traefik", db=_db())
    v = registry.get("generic").helm_values(ctx)
    assert v["ingress"]["enabled"] is True
    assert v["database"]["existingSecret"] == "odoo-db-app"


def test_unknown_adapter_falls_back_to_generic():
    assert registry.get("does-not-exist") is registry.get("generic")
