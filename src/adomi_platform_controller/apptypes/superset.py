"""Superset app-type adapter (upstream apache/superset chart).

Maps the platform inputs into the upstream chart's value shape: ingress, an external
(controller-managed) metadata database, and the OAuth2 secret name for native OIDC.

NOTE: the upstream Superset chart has a large, version-specific value surface
(redis bundling, configOverrides for OIDC, datasource bootstrap). This adapter wires
the parts the platform owns (ingress + metadata DB + SSO secret reference); the
SECRET_KEY and OIDC configOverride are supplied via the ApplicationType.defaultValues
and finalized against the chart version on a live cluster.
"""

from __future__ import annotations

from . import base


class SupersetAdapter:
    def helm_values(self, ctx: base.Ctx) -> dict:
        ingress: dict = {
            "enabled": True,
            "ingressClassName": ctx.ingress_class_name,
            "hosts": [ctx.host],
            "path": "/",
        }

        if ctx.ingress_tls:
            ingress["tls"] = ctx.ingress_tls

        values: dict = {
            "ingress": ingress,
        }

        # Use the controller-managed metadata database (disable the bundled one).
        if ctx.db is not None:
            values["postgresql"] = {"enabled": False}

            values["supersetNode"] = {
                "connections": {
                    "db_host": ctx.db.host,
                    "db_port": str(ctx.db.port),
                    "db_name": ctx.db.name,
                    "db_user": ctx.db.user,
                }
            }

            # The DB password is provided to Superset via an env var sourced from the
            # CNPG/external Secret (the chart's configOverrides build the URI from it).
            values["extraEnvRaw"] = [
                {
                    "name": "DB_PASS",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": ctx.db.password_secret_name,
                            "key": ctx.db.password_secret_key,
                        }
                    },
                }
            ]

        if ctx.sso_protocol == "oauth2" and ctx.sso_secret:
            # OIDC client credentials Secret (client-id / client-secret) for the
            # chart's configOverrides to consume.
            values["extraEnvRaw"] = values.get("extraEnvRaw", []) + [
                {
                    "name": "OIDC_CLIENT_ID",
                    "valueFrom": {"secretKeyRef": {"name": ctx.sso_secret, "key": "client-id"}},
                },
                {
                    "name": "OIDC_CLIENT_SECRET",
                    "valueFrom": {"secretKeyRef": {"name": ctx.sso_secret, "key": "client-secret"}},
                },
            ]

        return values

    def connection(self, ctx: base.Ctx) -> dict:
        return {
            "url": ctx.url,
        }
