"""Push CR status changes to the Odoo management portal.

Registers a ``@kopf.on.field`` watcher on ``status`` for every platform CRD. When
a resource's status changes (which is exactly when the controller updates Ready /
phase / URL during reconcile), the new object is POSTed to the Odoo portal so it
reflects live state immediately instead of polling. The push is best-effort and
never raises, so it cannot disturb the owning reconcile.
"""

from __future__ import annotations

import kopf

from .. import odoonotify

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"


def _make_handler(plural: str, model: str):
    @kopf.on.field(GROUP, VERSION, plural, field="status", id=f"odoo-sync-{plural}")
    def _push(name, body, **_):
        odoonotify.push_status(model, name, dict(body))

    return _push


# Build a status watcher per CRD. Names are unique via the per-plural handler id.
_HANDLERS = [_make_handler(plural, model) for plural, model in odoonotify.MODEL_BY_PLURAL.items()]
