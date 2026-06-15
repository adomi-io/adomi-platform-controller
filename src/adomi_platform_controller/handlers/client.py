"""ClientReconciler.

A Client is the primary business entity: it groups an end customer's Odoo projects
and environments. It is a lightweight resource — the reconciler resolves its slug,
optionally checks that a referenced Organization exists, and records status. Projects
and environments reference a Client by name.
"""

from __future__ import annotations

import kopf

from .. import conditions, resolve, state
from ._common import fail

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"
PLURAL = "clients"


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def reconcile(spec, meta, status, patch, name, **_) -> None:
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
    conditions.mark_ready(patch, status, f"Client {slug!r} reconciled", generation)
