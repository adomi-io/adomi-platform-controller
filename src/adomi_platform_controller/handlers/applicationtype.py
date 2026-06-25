"""ApplicationTypeReconciler.

An ApplicationType is a cluster-scoped catalog entry: the chart an Application of
this type deploys, the code adapter that maps platform inputs into that chart's
values, and the type's capabilities. It is configuration-only — the reconciler
validates the entry and records status.
"""

from __future__ import annotations

import kopf

from .. import conditions, state
from ..apptypes import registry
from ._common import Reconciler, fail


class ApplicationTypeReconciler(Reconciler):
    plural = "applicationtypes"

    def reconcile(self, spec, meta, status, patch, name, **_) -> None:
        generation = meta.get("generation", 0)
        state.provider()

        chart = spec.get("chart") or {}

        if not chart.get("repoURL"):
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "chart.repoURL is required",
                generation,
            )

        adapter = spec.get("adapter") or registry.GENERIC
        msg = f"ApplicationType {name!r} ready (adapter={adapter})"

        if adapter not in (registry.GENERIC, "odoo", "superset", "mailpit"):
            msg += " — no built-in adapter, using generic"

        conditions.mark_ready(patch, status, msg, generation)


_reconciler = ApplicationTypeReconciler()


@kopf.on.create(
    ApplicationTypeReconciler.GROUP,
    ApplicationTypeReconciler.VERSION,
    ApplicationTypeReconciler.plural,
)
@kopf.on.update(
    ApplicationTypeReconciler.GROUP,
    ApplicationTypeReconciler.VERSION,
    ApplicationTypeReconciler.plural,
)
@kopf.on.resume(
    ApplicationTypeReconciler.GROUP,
    ApplicationTypeReconciler.VERSION,
    ApplicationTypeReconciler.plural,
)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)
