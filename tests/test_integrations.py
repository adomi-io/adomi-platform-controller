"""Tests for the integration connectors (pure value injectors)."""

from __future__ import annotations

from adomi_platform_controller.apptypes import base
from adomi_platform_controller.integrations import registry

_CTX = base.Ctx("consumer", "acme-production", "h", "https://h", "traefik")


def test_registry_lookup():
    assert registry.get("odoo-mailpit-smtp") is not None
    assert registry.get("odoo-superset-datasource") is not None
    assert registry.get("nope") is None


def test_odoo_mailpit_smtp():
    conn = {"smtp": {"host": "mailpit.acme-production.svc.cluster.local", "port": 1025}}
    vals = registry.get("odoo-mailpit-smtp").values(conn, _CTX)
    assert vals["extraEnv"] == [
        {"name": "ODOO_SMTP_SERVER", "value": "mailpit.acme-production.svc.cluster.local"},
        {"name": "ODOO_SMTP_PORT", "value": "1025"},
    ]


def test_odoo_mailpit_smtp_missing_connection():
    assert registry.get("odoo-mailpit-smtp").values({}, _CTX) == {}


def test_odoo_superset_datasource():
    conn = {
        "db": {
            "host": "odoo-db-rw.acme-production.svc.cluster.local",
            "port": 5432,
            "name": "app",
            "user": "app",
            "passwordSecret": "odoo-db-app",
            "passwordSecretKey": "password",
        }
    }
    vals = registry.get("odoo-superset-datasource").values(conn, _CTX)
    assert vals["extraEnv"]["ODOO_DB_HOST"] == "odoo-db-rw.acme-production.svc.cluster.local"
    assert vals["extraEnv"]["ODOO_DB_NAME"] == "app"
    raw = vals["extraEnvRaw"][0]
    assert raw["name"] == "ODOO_DB_PASS"
    assert raw["valueFrom"]["secretKeyRef"]["name"] == "odoo-db-app"
