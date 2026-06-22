"""Odoo app-type adapter (the adomi-helm odoo chart)."""

from __future__ import annotations

from . import base


class OdooAdapter:
    def helm_values(self, ctx: base.Ctx) -> dict:
        addons = (ctx.odoo.get("addons") or {}) if ctx.odoo else {}

        odoo: dict = {
            "workers": int((ctx.odoo or {}).get("workers") or 0),
            "proxyMode": True,
            "listDb": ctx.list_db,
            "withoutDemo": True,
        }

        if addons.get("initModules"):
            odoo["initModules"] = addons["initModules"]

        if addons.get("updateModules"):
            odoo["updateModules"] = addons["updateModules"]

        values: dict = {
            "replicaCount": ctx.replicas,
            "image": base.image_block(ctx),
            # The controller owns the database; never run the chart's bundled Postgres.
            "postgresql": {"enabled": False},
            "database": base.existing_secret_db(ctx),
            "ingress": base.standard_ingress(ctx, longpolling=ctx.longpolling),
            "odoo": odoo,
        }

        if not values["image"]:
            del values["image"]

        if ctx.admin_password:
            values["adminPassword"] = ctx.admin_password

        return values

    def connection(self, ctx: base.Ctx) -> dict:
        conn: dict = {
            "url": ctx.url,
        }

        if ctx.db is not None:
            conn["db"] = {
                "host": ctx.db.host,
                "port": ctx.db.port,
                "name": ctx.db.name,
                "user": ctx.db.user,
                "passwordSecret": ctx.db.password_secret_name,
                "passwordSecretKey": ctx.db.password_secret_key,
            }

        return conn
