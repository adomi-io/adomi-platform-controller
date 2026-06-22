"""DomainReconciler.

A Domain is a DNS domain a customer's apps are published under
(``Application.spec.domainRef``). Apps get hostnames as ``<label>.<fqdn>`` and
cert-manager issues per-ingress certificates from the platform ClusterIssuer (via the
ingress annotation the app sets), so this reconciler is intentionally lightweight: it
validates the domain and publishes the resolved base host. Wildcard pre-provisioning
is a future enhancement.
"""

from __future__ import annotations

import kopf

from .. import conditions, state
from ._common import Reconciler, fail


class DomainReconciler(Reconciler):
    plural = "domains"

    def reconcile(self, spec, meta, status, patch, name, **_) -> None:
        generation = meta.get("generation", 0)
        state.provider()

        fqdn = (spec.get("fqdn") or "").strip().lower()

        if not fqdn:
            fail(patch, status, conditions.REASON_INVALID_SPEC, "fqdn is required", generation)

        patch.status["host"] = fqdn

        conditions.mark_ready(patch, status, f"Domain {fqdn!r} ready", generation)


_reconciler = DomainReconciler()


@kopf.on.create(DomainReconciler.GROUP, DomainReconciler.VERSION, DomainReconciler.plural)
@kopf.on.update(DomainReconciler.GROUP, DomainReconciler.VERSION, DomainReconciler.plural)
@kopf.on.resume(DomainReconciler.GROUP, DomainReconciler.VERSION, DomainReconciler.plural)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)
