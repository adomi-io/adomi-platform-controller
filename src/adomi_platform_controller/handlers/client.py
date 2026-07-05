"""ClientReconciler.

A Client is the primary business entity: it groups an end customer's Odoo projects
and environments. It is a lightweight resource — the reconciler resolves its slug,
optionally checks that a referenced Organization exists, and records status. Projects
and environments reference a Client by name.
"""

from __future__ import annotations

import kopf

from .. import conditions, requeue, resolve, state
from ._common import Reconciler, fail


class ClientReconciler(Reconciler):
    plural = "clients"

    def reconcile(self, spec, meta, status, patch, name, namespace, logger, **_) -> None:
        generation = meta.get("generation", 0)
        state.provider()  # ensure the backend/config singleton is initialised

        slug = (spec.get("slug") or name).strip()

        org_ref = (spec.get("organizationRef") or {}).get("name")

        if org_ref:
            try:
                resolve.get_organization(org_ref)
            except resolve.NotFound as exc:
                fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)

        patch.status["slug"] = slug

        # Applications inherit this Client's variables: re-render them on a spec
        # change (idempotent per generation, so restarts/status writes are no-ops).
        requeue.requeue_applications(
            requeue.revision("client", name, generation), namespace=namespace, logger=logger
        )

        conditions.mark_ready(patch, status, f"Client {slug!r} reconciled", generation)


_reconciler = ClientReconciler()


@kopf.on.create(ClientReconciler.GROUP, ClientReconciler.VERSION, ClientReconciler.plural)
@kopf.on.update(ClientReconciler.GROUP, ClientReconciler.VERSION, ClientReconciler.plural)
@kopf.on.resume(ClientReconciler.GROUP, ClientReconciler.VERSION, ClientReconciler.plural)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)
