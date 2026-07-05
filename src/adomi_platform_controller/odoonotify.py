"""Push platform CR status changes to the Odoo management portal.

Odoo is the primary portal for running the platform, so it needs to reflect a
resource's live state (Ready / phase / URL) as soon as it changes. Rather than
have Odoo poll the Kubernetes API on a cron, the controller POSTs the changed
object to the portal's ingest endpoint whenever a CR's ``status`` changes (see
``handlers/odoo_sync.py``). The portal's cron remains only as a slow fallback.

The call is authenticated with a shared bearer token read from OpenBao (it must
match the portal's ``ADOMI_INGEST_TOKEN``) and is strictly best-effort: any
failure is logged and swallowed so it never blocks or fails a reconcile. Built on
the standard-library ``urllib`` to avoid adding a dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from . import state

_logger = logging.getLogger(__name__)

# Endpoint exposed by the adomi_platform Odoo addon (controllers/main.py).
INGEST_PATH = "/adomi_platform/ingest"

# Map each platform CRD plural to the Odoo model that mirrors it.
MODEL_BY_PLURAL: dict[str, str] = {
    "organizations": "adomi.organization",
    "clients": "adomi.client",
    "domains": "adomi.domain",
    "environments": "adomi.environment",
    "databaseservers": "adomi.database.server",
    "applications": "adomi.application",
    "applicationtypes": "adomi.application.type",
    "gitrepositories": "adomi.git.repository",
    "snapshots": "adomi.snapshot",
}


def _token(cfg) -> str:
    bao = state.provider().openbao()
    data = bao.read(cfg.odoo_notify_secret_path) or {}

    return (data.get(cfg.odoo_notify_token_key) or "").strip()


def push_status(model: str, name: str, obj: dict) -> None:
    """Best-effort POST of a CR's current state to the Odoo portal.

    ``obj`` is the full custom-resource body; the portal extracts what each model
    needs (Ready condition, phase, URL). Never raises.
    """
    cfg = state.provider().config

    if not cfg.odoo_notify_enabled():
        return

    try:
        token = _token(cfg)

        if not token:
            _logger.warning(
                "Odoo status push skipped: no token at OpenBao %r", cfg.odoo_notify_secret_path
            )

            return

        payload = json.dumps(
            {
                "model": model,
                "name": name,
                "object": obj,
            }
        ).encode()
        url = cfg.odoo_notify_url.rstrip("/") + INGEST_PATH

        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token}")

        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - in-cluster service URL
            resp.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _logger.warning("Odoo status push failed for %s/%s: %s", model, name, exc)
