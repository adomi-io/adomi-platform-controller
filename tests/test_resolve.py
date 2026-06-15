"""Tests for Application effective-config resolution and pure helpers."""

from __future__ import annotations

from adomi_platform_controller import resolve
from adomi_platform_controller.config import Config

_TYPE = {
    "adapter": "odoo",
    "chart": {
        "repoURL": "https://git/adomi-helm",
        "path": "charts/odoo",
        "targetRevision": "master",
    },
    "database": {"required": True},
    "sso": {"enabled": True, "protocol": "proxy"},
    "ingress": {"longpolling": True},
    "defaultValues": {"odoo": {"logLevel": "info"}},
    "provides": ["url", "db"],
}


def _eff(*, org=None, client=None, ws=None, app=None, type_=None, app_name="odoo", cfg=None):
    return resolve.compute(
        cfg or Config(base_domain="adomi.io"),
        org_spec=org,
        client_name="acme",
        client_spec=client or {},
        workspace_name="production",
        workspace_spec=ws or {"class": "production"},
        app_name=app_name,
        app_spec=app or {},
        type_spec=type_ if type_ is not None else _TYPE,
    )


def test_compute_defaults():
    eff = _eff()
    assert eff.client_slug == "acme"
    assert eff.workspace_name == "production"
    assert eff.namespace == "acme-production"
    assert eff.hostname == "odoo-production-acme.adomi.io"
    assert eff.url == "https://odoo-production-acme.adomi.io"
    assert eff.adapter == "odoo"
    assert eff.chart_path == "charts/odoo"
    assert eff.db_mode == resolve.DB_MODE_CNPG
    assert eff.longpolling is True
    assert eff.sso_enabled is True
    assert eff.sso_protocol == "proxy"
    assert eff.type_defaults == {"odoo": {"logLevel": "info"}}


def test_compute_host_override_and_sso_disable():
    eff = _eff(app={"ingress": {"host": "odoo.acme.com"}, "sso": {"enabled": False}})
    assert eff.hostname == "odoo.acme.com"
    assert eff.sso_enabled is False


def test_compute_db_mode_explicit_and_none_type():
    assert _eff(app={"database": {"mode": "external"}}).db_mode == resolve.DB_MODE_EXTERNAL
    no_db_type = dict(_TYPE, database={"required": False})
    assert _eff(type_=no_db_type).db_mode == resolve.DB_MODE_NONE


def test_helpers():
    assert resolve.namespace_name("acme", "production") == "acme-production"
    assert resolve.parse_owner_repo("git@github.com:acme/erp.git") == ("acme", "erp")
    assert resolve.sanitize_tag("feature/x") == "feature-x"
    assert resolve.built_image_ref("h", "previews", "acme", "odoo", "main") == (
        "h/previews/acme-odoo:main"
    )
    assert resolve.sanitize_default("production") is False
    assert resolve.sanitize_default("pdi") is True
    assert resolve.snapshot_object_key("ns", "snap") == "snapshots/ns/snap.pgdump"
    assert resolve.cnpg_cluster_name("odoo") == "odoo-db"


def test_resolve_db_mode():
    assert resolve.resolve_db_mode({"mode": "cnpg"}, {"required": False}) == "cnpg"
    assert resolve.resolve_db_mode({}, {"required": True}) == "cnpg"
    assert resolve.resolve_db_mode({}, {}) == "none"


def test_deep_merge():
    merged = resolve.deep_merge({"a": {"x": 1}}, {"a": {"y": 2}, "b": 3}, {"b": 4})
    assert merged == {"a": {"x": 1, "y": 2}, "b": 4}


def test_app_db_connection_cnpg():
    app = {
        "metadata": {"name": "odoo", "namespace": "adomi-system"},
        "spec": {"database": {"mode": "cnpg"}},
        "status": {"namespace": "acme-production", "databaseMode": "cnpg"},
    }
    conn = resolve.app_db_connection(app)
    assert conn.host == "odoo-db-rw.acme-production.svc.cluster.local"
    assert conn.password_secret_name == "odoo-db-app"
    assert conn.password_secret_namespace == "acme-production"


def test_app_db_connection_external():
    app = {
        "metadata": {"name": "odoo", "namespace": "adomi-system"},
        "spec": {
            "database": {
                "mode": "external",
                "external": {
                    "host": "db.example.com",
                    "port": 25060,
                    "passwordSecret": {"name": "managed", "key": "password"},
                },
            }
        },
        "status": {"namespace": "acme-production", "databaseMode": "external"},
    }
    conn = resolve.app_db_connection(app)
    assert conn.host == "db.example.com"
    assert conn.port == 25060
    assert conn.password_secret_namespace == "adomi-system"
    assert conn.password_secret_name == "managed"
