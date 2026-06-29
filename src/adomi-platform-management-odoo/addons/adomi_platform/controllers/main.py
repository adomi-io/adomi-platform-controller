"""HTTP ingest endpoint for status pushes from the platform controller.

The controller POSTs a platform.adomi.io custom resource here whenever its status
changes, so the portal reflects live state immediately instead of polling. The
request is authenticated with a shared bearer token (``ADOMI_INGEST_TOKEN`` env, or
the ``adomi_platform.ingest_token`` config parameter as a fallback) that must match
the token the controller reads from OpenBao.

POST /adomi_platform/ingest
    Authorization: Bearer <token>
    {"model": "adomi.application", "name": "<k8s metadata.name>", "object": {<CR>}}
"""

import json
import logging
import os

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Only these models may be targeted (they all mirror a platform.adomi.io CRD and
# implement ingest_status via the k8s mixin).
ALLOWED_MODELS = {
    "adomi.organization",
    "adomi.client",
    "adomi.workspace",
    "adomi.database.server",
    "adomi.application.type",
    "adomi.application",
    "adomi.git.repository",
    "adomi.snapshot",
}


def _json(payload, status=200):
    return request.make_response(
        json.dumps(payload),
        headers=[("Content-Type", "application/json")],
        status=status,
    )


def _expected_token():
    token = os.environ.get("ADOMI_INGEST_TOKEN")

    if token:
        return token.strip()

    return (
        request.env["ir.config_parameter"].sudo().get_param("adomi_platform.ingest_token") or ""
    ).strip()


class AdomiIngestController(http.Controller):
    @http.route(
        "/adomi_platform/ingest",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def ingest(self, **_kw):
        header = request.httprequest.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        expected = _expected_token()

        if not expected or token != expected:
            return _json({"error": "unauthorized"}, status=401)

        try:
            data = json.loads(request.httprequest.get_data() or b"{}")
        except (ValueError, TypeError):
            return _json({"error": "invalid json"}, status=400)

        model = data.get("model")
        name = data.get("name")
        obj = data.get("object") or {}

        if model not in ALLOWED_MODELS or not name:
            return _json({"error": "bad request"}, status=400)

        try:
            updated = request.env[model].sudo().ingest_status(name, obj)
        except Exception:  # noqa: BLE001 - never leak internals to the caller
            _logger.exception("Adomi ingest failed for %s/%s", model, name)

            return _json({"error": "internal error"}, status=500)

        return _json({"ok": bool(updated), "matched": bool(updated)})
