"""Generic app-type adapter.

For catalog charts that follow the platform's standard value interface (image,
ingress, database existingSecret). New simple apps (uptime-kuma, vaultwarden, …) can
use this without bespoke controller code — they only need an ApplicationType entry.
"""

from __future__ import annotations

from . import base


class GenericAdapter:
    def helm_values(self, ctx: base.Ctx) -> dict:
        values: dict = {
            "replicaCount": ctx.replicas,
            "ingress": base.standard_ingress(ctx),
        }

        image = base.image_block(ctx)
        if image:
            values["image"] = image

        db = base.existing_secret_db(ctx)
        if db:
            values["database"] = db

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
