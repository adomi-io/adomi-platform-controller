"""HTTP endpoints for pushes from the platform: controller status + git webhooks.

The controller POSTs a platform.adomi.io custom resource here whenever its status
changes, so the portal reflects live state immediately instead of polling. The
request is authenticated with a shared bearer token (``ADOMI_INGEST_TOKEN`` env, or
the ``adomi_platform.ingest_token`` config parameter as a fallback) that must match
the token the controller reads from OpenBao.

POST /adomi_platform/ingest
    Authorization: Bearer <token>
    {"model": "adomi.application", "name": "<k8s metadata.name>", "object": {<CR>}}

Forgejo additionally POSTs push events for the client infrastructure repos (an
org-level webhook kept in place by openbao-bootstrap), so git edits show up in
the portal within moments instead of on the hourly fallback cron. Those requests
are authenticated with the webhook's HMAC-SHA256 body signature
(``ADOMI_FORGEJO_WEBHOOK_SECRET`` env, or the
``adomi_platform.forgejo_webhook_secret`` config parameter as a fallback).

POST /adomi_platform/webhook/forgejo
    X-Forgejo-Event: push
    X-Forgejo-Signature: <hex hmac-sha256 of the raw body>
"""

import hashlib
import hmac
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
    "adomi.domain",
    "adomi.environment",
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


def _forgejo_webhook_secret():
    secret = os.environ.get("ADOMI_FORGEJO_WEBHOOK_SECRET")

    if secret:
        return secret.strip()

    return (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param("adomi_platform.forgejo_webhook_secret")
        or ""
    ).strip()


def valid_forgejo_signature(raw, secret, signature):
    """True when ``signature`` is the hex HMAC-SHA256 of ``raw`` under ``secret``."""
    if not secret or not signature:
        return False

    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    return hmac.compare_digest(digest, signature.strip().lower())


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

    @http.route(
        "/adomi_platform/webhook/forgejo",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def forgejo_webhook(self, **_kw):
        raw = request.httprequest.get_data() or b""
        headers = request.httprequest.headers
        signature = headers.get("X-Forgejo-Signature") or headers.get("X-Gitea-Signature") or ""

        if not valid_forgejo_signature(raw, _forgejo_webhook_secret(), signature):
            return _json({"error": "unauthorized"}, status=401)

        event = headers.get("X-Forgejo-Event") or headers.get("X-Gitea-Event") or ""

        if event != "push":
            return _json({"ok": True, "ignored": event or "unknown"})

        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, TypeError):
            return _json({"error": "invalid json"}, status=400)

        repo = ((payload.get("repository") or {}).get("name") or "").strip()

        try:
            matched = request.env["adomi.client"].sudo()._on_repo_push(repo)
        except Exception:  # noqa: BLE001 - never leak internals to the caller
            _logger.exception("Adomi Forgejo webhook failed for repo %s", repo)

            return _json({"error": "internal error"}, status=500)

        return _json({"ok": True, "matched": bool(matched)})
