"""GitHub App onboarding + webhook endpoints.

The manifest flow is a browser round-trip:
  Odoo  --(auto-POST manifest)-->  GitHub  --(redirect ?code)-->  manifest_callback
  manifest_callback converts the code into App credentials, then sends the
  operator to install the App; GitHub redirects back to /setup with an
  installation_id we capture. /webhook receives signed lifecycle events.
"""

import json
import logging

from odoo import http
from odoo.http import request

from ..models import github_client

_logger = logging.getLogger(__name__)


class GithubOnboardingController(http.Controller):
    # --- step 1: render the manifest and auto-submit it to GitHub ---
    @http.route("/adomi_platform/github/manifest_new/<int:app_id>", type="http", auth="user")
    def manifest_new(self, app_id, **kw):
        app = request.env["adomi.github.app"].browse(app_id).exists()
        if not app:
            return request.not_found()
        manifest = json.dumps(app._manifest())
        state = app.sudo().manifest_state or ""
        action = "https://github.com/settings/apps/new?state=%s" % state
        # Auto-submitting form: GitHub requires the manifest via a POST form field.
        html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Redirecting to GitHub…</title></head>
<body onload="document.forms[0].submit()">
  <p>Redirecting to GitHub to create your app…</p>
  <form action="%s" method="post">
    <input type="hidden" name="manifest" value='%s'>
    <noscript><button type="submit">Continue to GitHub</button></noscript>
  </form>
</body></html>""" % (action, manifest.replace("'", "&#39;"))
        return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])

    # --- step 2: GitHub created the app, sends us a temporary code ---
    @http.route("/adomi_platform/github/manifest_callback", type="http", auth="user")
    def manifest_callback(self, code=None, state=None, **kw):
        app = request.env["adomi.github.app"].sudo().search(
            [("manifest_state", "=", state)], limit=1
        )
        if not app or not code:
            return request.redirect("/odoo/action-adomi_platform.action_adomi_github_app")
        try:
            import requests

            data = github_client.convert_manifest(code, requests)
            app._apply_manifest_conversion(data)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("GitHub manifest conversion failed")
            app.write({"state": "error", "last_error": str(exc)})
            return request.redirect(
                "/odoo/action-adomi_platform.action_adomi_github_app/%s" % app.id
            )
        # Straight into installation.
        return request.redirect(app.action_install()["url"])

    # --- step 3: GitHub finished the install, hands us the installation_id ---
    @http.route("/adomi_platform/github/setup", type="http", auth="user")
    def setup(self, installation_id=None, setup_action=None, state=None, **kw):
        app = request.env["adomi.github.app"].sudo().search(
            [("manifest_state", "=", state)], limit=1
        )
        if not app:
            app = request.env["adomi.github.app"].sudo().search([], order="create_date desc", limit=1)
        if app:
            try:
                app.action_sync_installations()
            except Exception:  # noqa: BLE001 - surfaced on the form instead
                _logger.exception("Installation sync after setup failed")
            return request.redirect(
                "/odoo/action-adomi_platform.action_adomi_github_app/%s" % app.id
            )
        return request.redirect("/odoo/action-adomi_platform.action_adomi_github_app")

    # --- ongoing: signed lifecycle events ---
    @http.route(
        "/adomi_platform/github/webhook", type="http", auth="public", methods=["POST"], csrf=False,
        save_session=False,
    )
    def webhook(self, **kw):
        body = request.httprequest.get_data() or b""
        signature = request.httprequest.headers.get("X-Hub-Signature-256", "")
        event = request.httprequest.headers.get("X-GitHub-Event", "")

        # Verify against any configured App's webhook secret.
        app = None
        for candidate in request.env["adomi.github.app"].sudo().search([("webhook_secret", "!=", False)]):
            if github_client.verify_webhook_signature(candidate.sudo().webhook_secret, body, signature):
                app = candidate
                break
        if not app:
            return request.make_response(json.dumps({"error": "bad signature"}), status=401,
                                         headers=[("Content-Type", "application/json")])

        try:
            payload = json.loads(body.decode() or "{}")
        except ValueError:
            payload = {}
        try:
            request.env["adomi.github.app"].sudo()._handle_webhook(app, event, payload)
        except Exception:  # noqa: BLE001 - never fail the delivery on our side
            _logger.exception("GitHub webhook handling failed (%s)", event)
        return request.make_response(json.dumps({"ok": True}),
                                     headers=[("Content-Type", "application/json")])
