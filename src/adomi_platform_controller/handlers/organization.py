"""OrganizationReconciler.

An Organization is a cluster-scoped, configuration-only resource: it holds
platform-wide defaults (base domain, default Odoo image repository, ingress class)
that Applications inherit. There is nothing to provision, so the reconciler
validates the spec and records the resolved defaults in status.
"""

from __future__ import annotations

import kopf

from .. import conditions, requeue, state
from ._common import Reconciler


class OrganizationReconciler(Reconciler):
    plural = "organizations"

    def reconcile(self, spec, meta, status, patch, name, logger, **_) -> None:
        generation = meta.get("generation", 0)
        cfg = state.provider().config

        base_domain = (spec.get("domain") or {}).get("base") or cfg.base_domain or ""
        image_repo = (spec.get("images") or {}).get("odooRepository") or cfg.odoo_image_repository

        patch.status["baseDomain"] = base_domain
        patch.status["odooImageRepository"] = image_repo

        # Every Application inherits the Organization's variables / base domain /
        # image defaults (unreferenced clients fall back to the single org too),
        # so a spec change re-renders them all (idempotent per generation).
        requeue.requeue_applications(
            requeue.revision("organization", name, generation), logger=logger
        )

        msg = f"Organization {name!r} reconciled"

        if not base_domain:
            msg += " (no base domain set; applications must declare spec.ingress.host)"

        conditions.mark_ready(patch, status, msg, generation)


_reconciler = OrganizationReconciler()


@kopf.on.create(
    OrganizationReconciler.GROUP, OrganizationReconciler.VERSION, OrganizationReconciler.plural
)
@kopf.on.update(
    OrganizationReconciler.GROUP, OrganizationReconciler.VERSION, OrganizationReconciler.plural
)
@kopf.on.resume(
    OrganizationReconciler.GROUP, OrganizationReconciler.VERSION, OrganizationReconciler.plural
)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)
