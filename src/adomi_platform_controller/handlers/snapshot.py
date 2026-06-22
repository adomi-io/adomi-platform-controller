"""SnapshotReconciler.

A Snapshot captures a point-in-time dump of an Application's Postgres database
to object storage (SeaweedFS S3). The reconciler resolves the source environment's
DB connection, ensures the S3 + DB-password Secrets in the argo namespace, submits
the ``odoo-db-snapshot`` Argo Workflow, and polls it to completion, recording the
object location in status.

On deletion the build Workflow is removed; the S3 object is retained (GC deferred).
"""

from __future__ import annotations

import kopf

from .. import conditions, dbjobs, resolve, state, workflows
from ._common import fail

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"
PLURAL = "snapshots"

POLL_DELAY = 15  # seconds while the dump runs
FAIL_DELAY = 120  # seconds after a failed dump


def _workflow_name(namespace: str, name: str) -> str:
    return f"snapshot-{namespace}-{name}"[:253]


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def reconcile(spec, meta, status, patch, name, namespace, logger, **_) -> None:
    generation = meta.get("generation", 0)
    cfg = state.provider().config

    app_ref = (spec.get("applicationRef") or {}).get("name")

    if not app_ref:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "applicationRef.name is required",
            generation,
        )

    try:
        app = resolve.get_application(app_ref, namespace)
        conn = resolve.app_db_connection(app)
    except resolve.NotFound as exc:
        fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)

    s3_key = resolve.snapshot_object_key(namespace, name)
    location = f"s3://{cfg.s3_bucket}/{s3_key}"
    wf_name = _workflow_name(namespace, name)

    try:
        db_secret, s3_secret = dbjobs.ensure_secrets(cfg, conn)
        workflows.apply(
            workflows.Spec(
                name=wf_name,
                namespace=cfg.argo_namespace,
                workflow_template_ref=cfg.snapshot_workflow_template,
                service_account=cfg.build_service_account,
                parameters=dbjobs.snapshot_params(cfg, conn, s3_key, db_secret, s3_secret),
                labels={"app.kubernetes.io/managed-by": "adomi-platform-controller"},
            )
        )
    except kopf.TemporaryError:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"submitting snapshot Workflow: {exc}",
            generation,
        )

    patch.status["sourceApplication"] = app_ref
    patch.status["workflow"] = wf_name
    patch.status["location"] = location

    ph = workflows.phase(workflows.get(wf_name, cfg.argo_namespace))

    if ph == workflows.PHASE_SUCCEEDED:
        patch.status["phase"] = "Completed"

        conditions.mark_ready(patch, status, f"Snapshot stored at {location}", generation)

        return

    if ph in (workflows.PHASE_FAILED, workflows.PHASE_ERROR):
        patch.status["phase"] = "Failed"

        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"snapshot Workflow {wf_name!r} {ph.lower()}",
            generation,
            delay=FAIL_DELAY,
        )

    patch.status["phase"] = "Running"

    fail(
        patch,
        status,
        conditions.REASON_RECONCILING,
        f"dumping database (Workflow {wf_name!r})",
        generation,
        delay=POLL_DELAY,
    )


@kopf.on.delete(GROUP, VERSION, PLURAL)
def finalize(status, name, namespace, logger, **_) -> None:
    """Delete the snapshot Workflow. The S3 object is retained (GC deferred)."""
    cfg = state.provider().config
    wf = status.get("workflow") or _workflow_name(namespace, name)

    try:
        workflows.delete(wf, cfg.argo_namespace)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed deleting snapshot Workflow {wf!r} during finalize: {exc}")

    if status.get("location"):
        logger.info(f"Snapshot object {status['location']} retained (object GC not implemented)")
