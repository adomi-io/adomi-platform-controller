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
from ._common import Reconciler, fail


class DatabaseReconciler(Reconciler):
    plural = "databases"

    @staticmethod
    def _client_slug(meta) -> str:
        return (meta.get("labels") or {}).get("platform.adomi.io/client") or ""

    def _target_namespace(self, meta, spec) -> str:
        """Where the CNPG cluster runs: the referenced environment's namespace, else
        the customer's shared data namespace."""
        client_slug = self._client_slug(meta)
        env = (spec.get("environmentRef") or {}).get("name")

        if env:
            return resolve.namespace_name(client_slug, env)

        return resolve._truncate_label(f"{client_slug}-data")

    def reconcile(self, spec, meta, status, patch, name, namespace, **_) -> None:
        generation = meta.get("generation", 0)
        state.provider()

        client_slug = self._client_slug(meta)

        if not client_slug:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "missing platform.adomi.io/client label",
                generation,
            )

        target_ns = self._target_namespace(meta, spec)
        labels = {
            "app.kubernetes.io/managed-by": self.MANAGED_BY,
            "platform.adomi.io/client": client_slug,
            "platform.adomi.io/database": name,
        }

        try:
            namespaces.Namespace(target_ns, labels).apply()
            cnpg.CnpgCluster(
                name=name,
                namespace=target_ns,
                instances=int(spec.get("instances") or 1),
                storage_size=spec.get("storage") or "10Gi",
                storage_class=spec.get("storageClass") or "",
                database=resolve.CNPG_DB_NAME,
                owner=resolve.CNPG_DB_USER,
                labels=labels,
            ).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"provisioning database: {exc}",
                generation,
            )

        patch.status["namespace"] = target_ns
        patch.status["connection"] = {
            "host": f"{name}{cnpg.CnpgCluster.RW_SERVICE_SUFFIX}.{target_ns}.svc.cluster.local",
            "port": resolve.DB_PORT,
            "name": resolve.CNPG_DB_NAME,
            "user": resolve.CNPG_DB_USER,
            "secretName": cnpg.CnpgCluster.app_secret_name(name),
            "secretKey": cnpg.CnpgCluster.APP_SECRET_PASSWORD_KEY,
        }

        conditions.mark_ready(patch, status, f"Database {name!r} ready in {target_ns}", generation)

    def finalize(self, spec, status, meta, name, logger, **_) -> None:
        """Delete the CNPG cluster (the namespace is shared, so leave it)."""
        ns = status.get("namespace") or self._target_namespace(meta, spec)

        try:
            cnpg.CnpgCluster.delete(name, ns)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed deleting CNPG cluster {name!r} during finalize: {exc}")


_reconciler = DatabaseReconciler()


@kopf.on.create(DatabaseReconciler.GROUP, DatabaseReconciler.VERSION, DatabaseReconciler.plural)
@kopf.on.update(DatabaseReconciler.GROUP, DatabaseReconciler.VERSION, DatabaseReconciler.plural)
@kopf.on.resume(DatabaseReconciler.GROUP, DatabaseReconciler.VERSION, DatabaseReconciler.plural)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)


@kopf.on.delete(DatabaseReconciler.GROUP, DatabaseReconciler.VERSION, DatabaseReconciler.plural)
def finalize(**kwargs) -> None:
    return _reconciler.finalize(**kwargs)
