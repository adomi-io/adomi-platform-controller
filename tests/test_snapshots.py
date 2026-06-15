"""Tests for the snapshot/restore workflow parameter builders."""

from __future__ import annotations

from adomi_platform_controller import dbjobs, resolve
from adomi_platform_controller.config import Config


def _conn() -> resolve.DbConnection:
    return resolve.DbConnection(
        host="odoo-db-rw.ns.svc.cluster.local",
        port=5432,
        name="app",
        user="app",
        password_secret_namespace="ns",
        password_secret_name="odoo-db-app",
        password_secret_key="password",
    )


def test_snapshot_params():
    cfg = Config()
    p = dbjobs.snapshot_params(cfg, _conn(), "snapshots/ns/snap.pgdump", "dbpass-ns", "platform-s3")
    assert p["dbHost"] == "odoo-db-rw.ns.svc.cluster.local"
    assert p["dbPort"] == "5432"
    assert p["dbSecret"] == "dbpass-ns"
    assert p["s3Key"] == "snapshots/ns/snap.pgdump"
    assert p["s3Secret"] == "platform-s3"
    assert p["pgImage"] == cfg.snapshot_postgres_image


def test_restore_params_sanitize_flag():
    cfg = Config()
    conn = _conn()
    on = dbjobs.restore_params(cfg, conn, "k", "db", "s3", "ghcr.io/x/odoo:19.0", True)
    off = dbjobs.restore_params(cfg, conn, "k", "db", "s3", "ghcr.io/x/odoo:19.0", False)
    assert on["sanitize"] == "true"
    assert off["sanitize"] == "false"
    assert on["odooImage"] == "ghcr.io/x/odoo:19.0"
