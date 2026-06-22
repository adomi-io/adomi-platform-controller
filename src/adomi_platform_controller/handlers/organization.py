"""OrganizationReconciler.

An Organization is a cluster-scoped, configuration-only resource: it holds
platform-wide defaults (base domain, default Odoo image repository, ingress class)
that Applications inherit. There is nothing to provision, so the reconciler
validates the spec and records the resolved defaults in status.
"""

from __future__ import annotations

import kopf

from .. import conditions, state

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"
PLURAL = "organizations"


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def reconcile(spec, meta, status, patch, name, **_) -> None:
    generation = meta.get("generation", 0)
    cfg = state.provider().config

    base_domain = (spec.get("domain") or {}).get("base") or cfg.base_domain or ""
    image_repo = (spec.get("images") or {}).get("odooRepository") or cfg.odoo_image_repository

    patch.status["baseDomain"] = base_domain
    patch.status["odooImageRepository"] = image_repo

    msg = f"Organization {name!r} reconciled"

    if not base_domain:
        msg += " (no base domain set; applications must declare spec.ingress.host)"

    conditions.mark_ready(patch, status, msg, generation)
