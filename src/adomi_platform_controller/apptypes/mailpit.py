"""Mailpit app-type adapter (the adomi-helm mailpit chart).

Mailpit is a developer mail trap: it accepts SMTP on 1025 and serves a web UI on
8025. It has no database. It publishes an ``smtp`` connection so other apps (e.g.
Odoo in a dev workspace) can route their outbound mail to it.
"""

from __future__ import annotations

from . import base

SMTP_PORT = 1025
WEB_PORT = 8025


class MailpitAdapter:
    def helm_values(self, ctx: base.Ctx) -> dict:
        values: dict = {
            "replicaCount": ctx.replicas,
            "ingress": base.standard_ingress(ctx),
        }

        image = base.image_block(ctx)
        if image:
            values["image"] = image

        return values

    def connection(self, ctx: base.Ctx) -> dict:
        # The mailpit chart names its Service after the release (the Application name).
        return {
            "url": ctx.url,
            "smtp": {
                "host": f"{ctx.app_name}.{ctx.namespace}.svc.cluster.local",
                "port": SMTP_PORT,
            },
        }
