"""Shared plumbing for the database snapshot/restore Argo Workflows.

Both jobs run in the argo namespace and reach an environment's Postgres over
cross-namespace service DNS. They need two Secrets in argo: the object-store
credentials (shared, read from OpenBao) and the environment's DB password (per
namespace, copied from the CNPG/external Secret). This module ensures those and
builds the (pure) Workflow parameter maps.
"""

from __future__ import annotations

from . import buildsecrets, state
from .config import Config
from .resolve import DbConnection

# Shared object-store credentials Secret in the argo namespace.
ARGO_S3_SECRET = "platform-s3"


def _db_secret_name(namespace: str) -> str:
    return f"dbpass-{namespace}"[:253]


def ensure_secrets(cfg: Config, conn: DbConnection) -> tuple[str, str]:
    """Ensure the S3-creds and DB-password Secrets exist in argo; return their names.

    Returns ``(db_secret_name, s3_secret_name)``.
    """
    bao = state.provider().openbao()
    s3 = bao.read(cfg.s3_secret_path) or {}
    access_key = (s3.get(cfg.s3_access_key_key) or "").strip()
    secret_key = (s3.get(cfg.s3_secret_key_key) or "").strip()
    if not access_key or not secret_key:
        raise RuntimeError(f"S3 credentials missing at OpenBao {cfg.s3_secret_path!r}")
    buildsecrets.ensure_opaque_secret(
        ARGO_S3_SECRET,
        cfg.argo_namespace,
        {"access-key": access_key, "secret-key": secret_key},
    )

    password = buildsecrets.read_key(
        conn.password_secret_name, conn.password_secret_namespace, conn.password_secret_key
    )
    db_secret = _db_secret_name(conn.password_secret_namespace)
    buildsecrets.ensure_opaque_secret(db_secret, cfg.argo_namespace, {"password": password})
    return db_secret, ARGO_S3_SECRET


def _db_params(conn: DbConnection, db_secret: str) -> dict[str, str]:
    return {
        "dbHost": conn.host,
        "dbPort": str(conn.port),
        "dbName": conn.name,
        "dbUser": conn.user,
        "dbSecret": db_secret,
    }


def _s3_params(cfg: Config, s3_key: str, s3_secret: str) -> dict[str, str]:
    return {
        "s3Endpoint": cfg.s3_endpoint,
        "s3Bucket": cfg.s3_bucket,
        "s3Key": s3_key,
        "s3Secret": s3_secret,
    }


def snapshot_params(
    cfg: Config, conn: DbConnection, s3_key: str, db_secret: str, s3_secret: str
) -> dict[str, str]:
    """Workflow parameters for the snapshot (dump → upload) job (pure)."""
    return {
        **_db_params(conn, db_secret),
        **_s3_params(cfg, s3_key, s3_secret),
        "pgImage": cfg.snapshot_postgres_image,
        "awscliImage": cfg.snapshot_awscli_image,
    }


def restore_params(
    cfg: Config,
    conn: DbConnection,
    s3_key: str,
    db_secret: str,
    s3_secret: str,
    odoo_image: str,
    sanitize: bool,
) -> dict[str, str]:
    """Workflow parameters for the restore (download → restore → sanitize) job (pure)."""
    return {
        **_db_params(conn, db_secret),
        **_s3_params(cfg, s3_key, s3_secret),
        "pgImage": cfg.snapshot_postgres_image,
        "awscliImage": cfg.snapshot_awscli_image,
        "odooImage": odoo_image,
        "sanitize": "true" if sanitize else "false",
    }
