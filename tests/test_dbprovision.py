"""Tests for the database provisioning SQL / Job and the resolve helpers."""

from __future__ import annotations

import pytest

from adomi_platform_controller import dbprovision, resolve


def test_validate_identifier_accepts_conventional_names():
    assert dbprovision.validate_identifier("acme_app_odoo_production") == "acme_app_odoo_production"
    assert dbprovision.validate_identifier("acme_app_odoo_production_user").endswith("_user")


@pytest.mark.parametrize(
    "bad",
    ["", "Acme", "1db", "has-dash", "has space", "a" * 64, 'drop";'],
)
def test_validate_identifier_rejects_unsafe(bad):
    with pytest.raises(dbprovision.InvalidIdentifier):
        dbprovision.validate_identifier(bad)


def test_build_sql_is_idempotent_and_parameterises_password():
    sql = dbprovision.build_sql("acme_app_odoo_production", "acme_app_odoo_production_user")

    # Guarded create (no unconditional CREATE DATABASE/ROLE) and \gexec for the db.
    assert "WHERE NOT EXISTS (SELECT 1 FROM pg_database" in sql
    assert "\\gexec" in sql
    assert "IF NOT EXISTS (SELECT 1 FROM pg_roles" in sql
    assert 'CREATE DATABASE "acme_app_odoo_production"' in sql
    assert (
        'ALTER DATABASE "acme_app_odoo_production" OWNER TO "acme_app_odoo_production_user"' in sql
    )
    # The password is read from the env into :pw via \getenv (never inlined, never an arg).
    assert f"\\getenv pw {dbprovision.NEW_PASSWORD_ENV}" in sql
    assert ":'pw'" in sql


def test_build_sql_appends_init_sql_reconnected_to_the_database():
    sql = dbprovision.build_sql(
        "appdb",
        "appuser",
        ["DO $$ BEGIN CREATE ROLE extra; END $$;", "CREATE EXTENSION IF NOT EXISTS citext;"],
    )
    # init SQL runs after a reconnect to the created database...
    assert '\\c "appdb"' in sql
    # ...and is delivered verbatim (anonymous $$ is safe — the script is run via psql -f,
    # never a shell), in order after the base provisioning.
    assert sql.index("ALTER DATABASE") < sql.index('\\c "appdb"') < sql.index("$$")
    assert "CREATE EXTENSION IF NOT EXISTS citext;" in sql


def test_provision_args_runs_the_mounted_sql_file():
    args = dbprovision.provision_args()
    assert "ON_ERROR_STOP=1" in args
    assert "-f" in args
    assert args[-1] == f"{dbprovision.SQL_MOUNT_PATH}/{dbprovision.SQL_FILENAME}"
    # The password is not passed as an argument (read via \getenv instead).
    assert not any("pw=" in a for a in args)


def test_build_sql_rejects_unsafe_identifiers():
    with pytest.raises(dbprovision.InvalidIdentifier):
        dbprovision.build_sql("ok_db", "bad-user")


def _job(**overrides) -> dbprovision.ProvisionJob:
    kwargs = dict(
        name="dbprov-acme",
        namespace="acme-data",
        image="postgres:16",
        host="acme-server-rw.acme-data.svc.cluster.local",
        port=5432,
        admin_secret="acme-server-superuser",
        database="acme_app_odoo_production",
        user="acme_app_odoo_production_user",
        user_secret="acme-app-odoo-owner",
        sql_configmap="dbprov-acme-sql",
    )
    kwargs.update(overrides)
    return dbprovision.ProvisionJob(**kwargs)


def test_provision_job_body_wires_admin_and_role_secrets():
    job = _job()
    body = job._body()
    container = body.spec.template.spec.containers[0]

    assert container.image == "postgres:16"
    assert body.spec.template.spec.restart_policy == "Never"

    env = {e.name: e for e in container.env}
    assert env["PGHOST"].value == "acme-server-rw.acme-data.svc.cluster.local"
    assert env["PGPORT"].value == "5432"
    assert env["PGUSER"].value_from.secret_key_ref.name == "acme-server-superuser"
    assert env["PGUSER"].value_from.secret_key_ref.key == "username"
    assert env["PGPASSWORD"].value_from.secret_key_ref.key == "password"
    assert env[dbprovision.NEW_PASSWORD_ENV].value_from.secret_key_ref.name == "acme-app-odoo-owner"


def test_provision_job_runs_sql_from_a_mounted_configmap():
    body = _job()._body()
    pod = body.spec.template.spec
    container = pod.containers[0]

    # The SQL is run from a file (no shell) — command is psql, with -f the mounted path.
    assert container.command == ["psql"]
    assert container.args[-1] == f"{dbprovision.SQL_MOUNT_PATH}/{dbprovision.SQL_FILENAME}"

    mount = container.volume_mounts[0]
    assert mount.mount_path == dbprovision.SQL_MOUNT_PATH and mount.read_only
    volume = pod.volumes[0]
    assert volume.name == mount.name == dbprovision.SQL_VOLUME
    assert volume.config_map.name == "dbprov-acme-sql"


def test_provision_job_hash_tracks_sql_so_it_recreates_on_change():
    base = _job()
    with_init = _job(init_sql=['GRANT windmill_admin TO "x";'])
    annotations = base._body().metadata.annotations
    assert annotations[dbprovision.ProvisionJob.HASH_ANNOTATION] == base._hash()
    # Changing the provisioning SQL changes the hash → apply() recreates the Job.
    assert base._hash() != with_init._hash()
    # And the Job's SQL payload (written to the ConfigMap) reflects the change.
    assert "GRANT windmill_admin" in with_init.sql


def test_provision_job_status_helpers():
    assert dbprovision.ProvisionJob.succeeded({"status": {"succeeded": 1}})
    assert dbprovision.ProvisionJob.succeeded(
        {"status": {"conditions": [{"type": "Complete", "status": "True"}]}}
    )
    assert dbprovision.ProvisionJob.failed(
        {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}
    )
    # A failed pod count alone is not terminal (the Job may still retry).
    assert not dbprovision.ProvisionJob.failed({"status": {"failed": 1}})
    assert not dbprovision.ProvisionJob.succeeded(None)


def test_db_credentials_path_keyed_by_server_and_user():
    path = resolve.db_credentials_path(
        "databases", "acme-prod-server", "acme_app_odoo_production_user"
    )
    assert path == "databases/acme-prod-server/acme_app_odoo_production_user"


def test_database_endpoint_requires_published_connection():
    with pytest.raises(resolve.NotFound):
        resolve.database_endpoint({"metadata": {"name": "db"}, "status": {}})


def test_database_endpoint_reads_status_connection():
    endpoint = resolve.database_endpoint(
        {
            "metadata": {"name": "acme-odoo"},
            "status": {
                "connection": {
                    "host": "acme-server-rw.acme-data.svc.cluster.local",
                    "port": 5432,
                    "name": "acme_app_odoo_production",
                    "user": "acme_app_odoo_production_user",
                    "openbaoPath": "databases/acme-prod-server/acme_app_odoo_production_user",
                    "passwordKey": "password",
                },
            },
        }
    )
    assert endpoint.host == "acme-server-rw.acme-data.svc.cluster.local"
    assert endpoint.name == "acme_app_odoo_production"
    assert endpoint.openbao_path.endswith("acme_app_odoo_production_user")
    assert endpoint.password_key == "password"
