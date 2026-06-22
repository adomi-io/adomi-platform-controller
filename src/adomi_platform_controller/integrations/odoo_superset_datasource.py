"""Connector: register a provider Odoo's database as a Superset data source.

Declared on the Superset Application; reads the Odoo Application's published ``db``
connection and injects the Odoo DB coordinates into Superset (host/port/name/user as
env, password via a Secret ref). A Superset bootstrap (catalog defaultValues
configOverride) imports a database named "odoo" from these ODOO_DB_* env vars.
"""

from __future__ import annotations

from ..apptypes import base


class OdooSupersetDatasource:
    consumer_type = "superset"
    provider_type = "odoo"

    def values(self, provider_connection: dict, ctx: base.Ctx) -> dict:
        db = (provider_connection or {}).get("db") or {}

        if not db.get("host"):
            return {}

        env = {
            "ODOO_DB_HOST": str(db["host"]),
            "ODOO_DB_PORT": str(db.get("port", 5432)),
            "ODOO_DB_NAME": str(db.get("name", "odoo")),
            "ODOO_DB_USER": str(db.get("user", "odoo")),
        }

        values: dict = {
            "extraEnv": env,
        }

        if db.get("passwordSecret"):
            values["extraEnvRaw"] = [
                {
                    "name": "ODOO_DB_PASS",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": db["passwordSecret"],
                            "key": db.get("passwordSecretKey", "password"),
                        }
                    },
                }
            ]

        return values
