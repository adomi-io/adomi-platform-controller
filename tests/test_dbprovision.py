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
    # The password is never inlined; psql reads it from the :'pw' variable.
    assert ":'pw'" in sql


def test_build_command_wraps_sql_in_psql_heredoc():
    cmd = dbprovision.build_command("appdb", "appuser")
    assert cmd[:2] == ["/bin/sh", "-c"]
    script = cmd[2]
    assert "psql -v ON_ERROR_STOP=1" in script
    assert f'-v pw="${dbprovision.NEW_PASSWORD_ENV}"' in script
    assert "<<'EOSQL'" in script and script.rstrip().endswith("EOSQL")


def test_build_sql_rejects_unsafe_identifiers():
    with pytest.raises(dbprovision.InvalidIdentifier):
        dbprovision.build_sql("ok_db", "bad-user")


def test_provision_job_body_wires_admin_and_role_secrets():
    job = dbprovision.ProvisionJob(
        name="dbprov-acme",
        namespace="acme-data",
        image="postgres:16",
        host="acme-server-rw.acme-data.svc.cluster.local",
        port=5432,
        admin_secret="acme-server-superuser",
        database="acme_app_odoo_production",
        user="acme_app_odoo_production_user",
        user_secret="acme-app-odoo-owner",
    )
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
