"""DatabaseReconciler.

A Database is a logical Postgres database (plus a login role) inside a DatabaseServer.
It belongs to an application — an Application attaches one via ``spec.databaseRef``. The
reconciler resolves the referenced server, generates the role's password once in
OpenBao (keyed by server+user, so apps sharing a role share its password), and runs a
one-shot Job that creates the database and role on the server as its admin. It then
publishes ``status.connection`` (the server host plus the OpenBao path of the password)
for consumers to read.
"""

from __future__ import annotations

import kopf

from .. import conditions, dbprovision, externalsecrets, resolve, secretgen, state
from ..buildsecrets import ManagedSecret
from ._common import Reconciler, fail

PROVISION_POLL_DELAY = 15  # seconds; requeue while the provisioning Job runs
PROVISION_FAIL_DELAY = 120  # seconds; back off after a failed provisioning Job


class DatabaseReconciler(Reconciler):
    plural = "databases"

    @staticmethod
    def _client_slug(meta) -> str:
        return (meta.get("labels") or {}).get("platform.adomi.io/client") or ""

    @staticmethod
    def _job_name(name: str) -> str:
        return f"dbprov-{name}"[:63].rstrip("-")

    @staticmethod
    def _owner_secret_name(name: str) -> str:
        return f"{name}-owner"[:253]

    def reconcile(self, spec, meta, status, patch, name, namespace, **_) -> None:
        generation = meta.get("generation", 0)
        provider = state.provider()
        cfg = provider.config

        client_slug = self._client_slug(meta)

        if not client_slug:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "missing platform.adomi.io/client label",
                generation,
            )

        server_ref = (spec.get("serverRef") or {}).get("name")
        database_name = spec.get("databaseName") or ""
        user = spec.get("user") or ""

        if not server_ref:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "serverRef.name is required",
                generation,
            )

        try:
            dbprovision.validate_identifier(database_name, "databaseName")
            dbprovision.validate_identifier(user, "user")
        except dbprovision.InvalidIdentifier as exc:
            fail(patch, status, conditions.REASON_INVALID_SPEC, str(exc), generation)

        # Resolve the server and require it to be reconciled enough to provision into.
        try:
            server = resolve.get_database_server(server_ref, namespace)
        except resolve.NotFound as exc:
            fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)

        srv_status = server.get("status") or {}
        server_ns = srv_status.get("namespace")
        server_host = srv_status.get("host")
        admin_secret = srv_status.get("adminSecretName")

        if not server_ns or not server_host or not admin_secret:
            fail(
                patch,
                status,
                conditions.REASON_DEPENDENCY_NOT_MET,
                f"DatabaseServer {server_ref!r} not ready (no published host/admin secret yet)",
                generation,
            )

        server_port = int(srv_status.get("port") or resolve.DB_PORT)
        ssl_mode = (((server.get("spec") or {}).get("external") or {}).get("sslMode") or "").strip()

        # Generate the role password once in OpenBao (shared users share their password).
        # credentials.openbaoPath overrides the derived location (explicit by design).
        creds_spec = spec.get("credentials") or {}
        path = creds_spec.get("openbaoPath") or resolve.db_credentials_path(
            cfg.database_credentials_path, server_ref, user
        )

        try:
            creds, _ = provider.openbao().ensure_keys(
                path,
                ["password"],
                lambda _key: secretgen.random_string(cfg.database_password_length),
            )
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"generating role password: {exc}",
                generation,
            )

        labels = {
            "app.kubernetes.io/managed-by": self.MANAGED_BY,
            "platform.adomi.io/client": client_slug,
            "platform.adomi.io/database": name,
        }

        # Deliver the password to the provisioning Job via a Secret in the server's
        # namespace (the canonical copy stays in OpenBao; generate-once keeps it stable).
        owner_secret = self._owner_secret_name(name)
        job_name = self._job_name(name)

        try:
            ManagedSecret.opaque(
                owner_secret,
                server_ns,
                {"password": creds["password"]},
                create_only=True,
            ).apply()

            dbprovision.ProvisionJob(
                name=job_name,
                namespace=server_ns,
                image=cfg.db_provision_image,
                host=server_host,
                port=server_port,
                admin_secret=admin_secret,
                database=database_name,
                user=user,
                user_secret=owner_secret,
                ssl_mode=ssl_mode,
                labels=labels,
            ).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"submitting provisioning Job: {exc}",
                generation,
            )

        patch.status["serverNamespace"] = server_ns

        job = dbprovision.ProvisionJob.read(job_name, server_ns)

        if dbprovision.ProvisionJob.failed(job):
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"provisioning Job {job_name!r} failed",
                generation,
                delay=PROVISION_FAIL_DELAY,
            )

        if not dbprovision.ProvisionJob.succeeded(job):
            fail(
                patch,
                status,
                conditions.REASON_RECONCILING,
                f"provisioning database (Job {job_name!r})",
                generation,
                delay=PROVISION_POLL_DELAY,
            )

        # Deliver the password into this Database's own namespace under the Secret name
        # the chart dictated (explicit wiring — the workload's env references this Secret).
        deliver_secret = creds_spec.get("secret") or ""
        password_key = creds_spec.get("passwordKey") or "password"

        if deliver_secret:
            try:
                externalsecrets.ExternalSecret(
                    name=deliver_secret,
                    namespace=namespace,
                    secret_name=deliver_secret,
                    store_name=cfg.cluster_secret_store,
                    remote_path=path,
                    data_map={password_key: "password"},
                    labels=labels,
                ).apply()
            except Exception as exc:  # noqa: BLE001
                fail(
                    patch,
                    status,
                    conditions.REASON_BACKEND_ERROR,
                    f"delivering database credentials: {exc}",
                    generation,
                )

        connection = {
            "host": server_host,
            "port": server_port,
            "name": database_name,
            "user": user,
            "openbaoPath": path,
            "passwordKey": password_key,
        }

        if deliver_secret:
            connection["secret"] = deliver_secret

        patch.status["connection"] = connection

        conditions.mark_ready(
            patch, status, f"Database {database_name!r} ready on {server_ref!r}", generation
        )

    def finalize(self, status, name, logger, **_) -> None:
        """Remove the provisioning Job + delivery Secret; the database/role (and its
        data) are intentionally left in place so deleting the CR never drops data."""
        ns = status.get("serverNamespace")

        if not ns:
            return

        try:
            dbprovision.ProvisionJob.delete(self._job_name(name), ns)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed deleting provisioning Job during finalize: {exc}")

        try:
            ManagedSecret.delete(self._owner_secret_name(name), ns)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed deleting owner Secret during finalize: {exc}")


_reconciler = DatabaseReconciler()


@kopf.on.create(DatabaseReconciler.GROUP, DatabaseReconciler.VERSION, DatabaseReconciler.plural)
@kopf.on.update(DatabaseReconciler.GROUP, DatabaseReconciler.VERSION, DatabaseReconciler.plural)
@kopf.on.resume(DatabaseReconciler.GROUP, DatabaseReconciler.VERSION, DatabaseReconciler.plural)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)


@kopf.on.delete(DatabaseReconciler.GROUP, DatabaseReconciler.VERSION, DatabaseReconciler.plural)
def finalize(**kwargs) -> None:
    return _reconciler.finalize(**kwargs)
