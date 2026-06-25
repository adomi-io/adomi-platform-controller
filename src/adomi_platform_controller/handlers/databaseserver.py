"""DatabaseServerReconciler.

A DatabaseServer is a Postgres server owned by a client. In ``cnpg`` mode the
reconciler provisions a CloudNativePG Cluster (in the referenced environment's
namespace, else the client's shared data namespace) with superuser access enabled so
the database provisioner can create databases and roles on it. In ``external`` mode it
points at an existing server (RDS / DigitalOcean) and materialises the admin
credentials from OpenBao into the server namespace via an ExternalSecret. Either way
it publishes ``status.host`` / ``status.adminSecretName`` so Databases can be created
inside it.
"""

from __future__ import annotations

import kopf

from .. import cnpg, conditions, externalsecrets, namespaces, resolve, state
from ._common import Reconciler, fail

MODE_CNPG = "cnpg"
MODE_EXTERNAL = "external"

ADMIN_SECRET_SUFFIX = "-admin"  # ExternalSecret/Secret name for an external admin


class DatabaseServerReconciler(Reconciler):
    plural = "databaseservers"

    @staticmethod
    def _client_slug(meta) -> str:
        return (meta.get("labels") or {}).get("platform.adomi.io/client") or ""

    def _server_namespace(self, meta, spec) -> str:
        """Where the server (and provisioning Jobs) run: the referenced environment's
        namespace, else the client's shared data namespace."""
        client_slug = self._client_slug(meta)
        env = (spec.get("environmentRef") or {}).get("name")

        if env:
            return resolve.namespace_name(client_slug, env)

        return resolve._truncate_label(f"{client_slug}-data")

    @classmethod
    def admin_secret_name(cls, name: str) -> str:
        return f"{name}{ADMIN_SECRET_SUFFIX}"

    def reconcile(self, spec, meta, status, patch, name, namespace, **_) -> None:
        generation = meta.get("generation", 0)
        cfg = state.provider().config

        client_slug = self._client_slug(meta)

        if not client_slug:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "missing platform.adomi.io/client label",
                generation,
            )

        mode = spec.get("mode") or MODE_CNPG
        server_ns = self._server_namespace(meta, spec)
        labels = {
            "app.kubernetes.io/managed-by": self.MANAGED_BY,
            "platform.adomi.io/client": client_slug,
            "platform.adomi.io/database-server": name,
        }

        try:
            namespaces.Namespace(server_ns, labels).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"ensuring namespace: {exc}",
                generation,
            )

        if mode == MODE_CNPG:
            host, port, admin_secret, admin_user = self._reconcile_cnpg(
                spec, name, server_ns, labels, patch, status, generation
            )
        elif mode == MODE_EXTERNAL:
            host, port, admin_secret, admin_user = self._reconcile_external(
                cfg, spec, name, server_ns, labels, patch, status, generation
            )
        else:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                f"unknown mode {mode!r} (expected 'cnpg' or 'external')",
                generation,
            )

        patch.status["namespace"] = server_ns
        patch.status["mode"] = mode
        patch.status["host"] = host
        patch.status["port"] = port
        patch.status["adminSecretName"] = admin_secret
        patch.status["adminUser"] = admin_user

        conditions.mark_ready(
            patch, status, f"DatabaseServer {name!r} ready in {server_ns}", generation
        )

    def _reconcile_cnpg(self, spec, name, server_ns, labels, patch, status, generation):
        cnpg_cfg = spec.get("cnpg") or {}

        try:
            cnpg.CnpgCluster(
                name=name,
                namespace=server_ns,
                instances=int(cnpg_cfg.get("instances") or 1),
                storage_size=cnpg_cfg.get("storage") or "10Gi",
                storage_class=cnpg_cfg.get("storageClass") or "",
                database=resolve.CNPG_DB_NAME,
                owner=resolve.CNPG_DB_USER,
                enable_superuser_access=True,
                labels=labels,
            ).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"provisioning cnpg cluster: {exc}",
                generation,
            )

        host = f"{cnpg.CnpgCluster.rw_host(name)}.{server_ns}.svc.cluster.local"

        return (
            host,
            resolve.DB_PORT,
            cnpg.CnpgCluster.superuser_secret_name(name),
            cnpg.CnpgCluster.SUPERUSER,
        )

    def _reconcile_external(self, cfg, spec, name, server_ns, labels, patch, status, generation):
        ext = spec.get("external") or {}
        admin = spec.get("admin") or {}
        host = (ext.get("host") or "").strip()
        openbao_path = (admin.get("openbaoPath") or "").strip()

        if not host:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "external.host is required for mode 'external'",
                generation,
            )

        if not openbao_path:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "admin.openbaoPath is required for mode 'external'",
                generation,
            )

        admin_secret = self.admin_secret_name(name)

        try:
            externalsecrets.ExternalSecret(
                name=admin_secret,
                namespace=server_ns,
                secret_name=admin_secret,
                store_name=cfg.cluster_secret_store,
                remote_path=openbao_path,
                data_map={"username": "username", "password": "password"},
                labels=labels,
            ).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"materialising admin credentials: {exc}",
                generation,
            )

        return (
            host,
            int(ext.get("port") or resolve.DB_PORT),
            admin_secret,
            admin.get("user") or cnpg.CnpgCluster.SUPERUSER,
        )

    def finalize(self, spec, status, meta, name, logger, **_) -> None:
        """Tear down what the controller created; the namespace is shared, so leave it."""
        ns = status.get("namespace") or self._server_namespace(meta, spec)
        mode = status.get("mode") or spec.get("mode") or MODE_CNPG

        if mode == MODE_CNPG:
            try:
                cnpg.CnpgCluster.delete(name, ns)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed deleting CNPG cluster {name!r} during finalize: {exc}")
        else:
            try:
                externalsecrets.ExternalSecret.delete(self.admin_secret_name(name), ns)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed deleting admin ExternalSecret during finalize: {exc}")


_reconciler = DatabaseServerReconciler()


@kopf.on.create(
    DatabaseServerReconciler.GROUP,
    DatabaseServerReconciler.VERSION,
    DatabaseServerReconciler.plural,
)
@kopf.on.update(
    DatabaseServerReconciler.GROUP,
    DatabaseServerReconciler.VERSION,
    DatabaseServerReconciler.plural,
)
@kopf.on.resume(
    DatabaseServerReconciler.GROUP,
    DatabaseServerReconciler.VERSION,
    DatabaseServerReconciler.plural,
)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)


@kopf.on.delete(
    DatabaseServerReconciler.GROUP,
    DatabaseServerReconciler.VERSION,
    DatabaseServerReconciler.plural,
)
def finalize(**kwargs) -> None:
    return _reconciler.finalize(**kwargs)
