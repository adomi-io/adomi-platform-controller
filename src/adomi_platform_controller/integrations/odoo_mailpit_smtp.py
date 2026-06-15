"""Connector: route a consumer Odoo's outbound mail to a provider Mailpit.

Declared on the Odoo Application; reads the Mailpit Application's published ``smtp``
connection and injects ODOO_SMTP_* env into the Odoo chart.
"""

from __future__ import annotations

from ..apptypes import base


class OdooMailpitSmtp:
    consumer_type = "odoo"
    provider_type = "mailpit"

    def values(self, provider_connection: dict, ctx: base.Ctx) -> dict:
        smtp = (provider_connection or {}).get("smtp") or {}
        host = smtp.get("host")
        port = smtp.get("port")
        if not host or not port:
            return {}
        return {
            "extraEnv": [
                {"name": "ODOO_SMTP_SERVER", "value": str(host)},
                {"name": "ODOO_SMTP_PORT", "value": str(port)},
            ]
        }
