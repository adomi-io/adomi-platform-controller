"""DatabaseReconciler.

A Database is a managed PostgreSQL (CloudNativePG) a customer can create on its own
and attach apps to (``Application.spec.databaseRef``). The reconciler provisions a
CNPG Cluster in the target workspace namespace (or the customer's shared data
namespace) and publishes the connection coordinates in ``status.connection`` for
consumers to read.
"""

from __future__ import annotations

import kopf

from .. import cnpg, conditions, namespaces, resolve, state
from ._common import fail

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"
PLURAL = "databases"

MANAGED_BY = "adomi-platform-controller"


def _client_slug(meta) -> str:
    return (meta.get("labels") or {}).get("platform.adomi.io/client") or ""


def _target_namespace(meta, spec) -> str:
    """Where the CNPG cluster runs: the referenced environment's namespace, else the
    customer's shared data namespace."""
    client_slug = _client_slug(meta)
    env = (spec.get("environmentRef") or {}).get("name")
    if env:
        return resolve.namespace_name(client_slug, env)
    return resolve._truncate_label(f"{client_slug}-data")


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def reconcile(spec, meta, status, patch, name, namespace, **_) -> None:
    generation = meta.get("generation", 0)
    state.provider()

    client_slug = _client_slug(meta)
    if not client_slug:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "missing platform.adomi.io/client label",
            generation,
        )

    target_ns = _target_namespace(meta, spec)
    labels = {
        "app.kubernetes.io/managed-by": MANAGED_BY,
        "platform.adomi.io/client": client_slug,
        "platform.adomi.io/database": name,
    }
    try:
        namespaces.ensure(target_ns, labels)
        cnpg.apply(
            cnpg.Spec(
                name=name,
                namespace=target_ns,
                instances=int(spec.get("instances") or 1),
                storage_size=spec.get("storage") or "10Gi",
                storage_class=spec.get("storageClass") or "",
                database=resolve.CNPG_DB_NAME,
                owner=resolve.CNPG_DB_USER,
                labels=labels,
            )
        )
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, f"provisioning database: {exc}", generation)

    patch.status["namespace"] = target_ns
    patch.status["connection"] = {
        "host": f"{name}{cnpg.RW_SERVICE_SUFFIX}.{target_ns}.svc.cluster.local",
        "port": resolve.DB_PORT,
        "name": resolve.CNPG_DB_NAME,
        "user": resolve.CNPG_DB_USER,
        "secretName": cnpg.app_secret_name(name),
        "secretKey": cnpg.APP_SECRET_PASSWORD_KEY,
    }
    conditions.mark_ready(patch, status, f"Database {name!r} ready in {target_ns}", generation)


@kopf.on.delete(GROUP, VERSION, PLURAL)
def finalize(spec, status, meta, name, logger, **_) -> None:
    """Delete the CNPG cluster (the namespace is shared, so leave it)."""
    ns = status.get("namespace") or _target_namespace(meta, spec)
    try:
        cnpg.delete(name, ns)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed deleting CNPG cluster {name!r} during finalize: {exc}")
