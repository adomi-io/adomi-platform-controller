"""ApplicationTypeReconciler.

An ApplicationType is a cluster-scoped catalog entry naming the chart an Application
of this type deploys. It is configuration-only — the reconciler validates the entry
and records status. (Value-shaping lives in the chart, not a controller adapter.)
"""

from __future__ import annotations

import kopf

from .. import conditions, state
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

        conditions.mark_ready(patch, status, f"ApplicationType {name!r} ready", generation)


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
