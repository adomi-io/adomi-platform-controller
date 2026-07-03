"""EnvironmentReconciler.

An Environment is a named environment for a Client (production / dev / pdi / preview /
test). It owns a namespace (``<client>-<environment>``) that its Applications deploy
into. The reconciler resolves the owning Client and ensures the namespace.
"""

from __future__ import annotations

import kopf

from .. import conditions, namespaces, resolve, state
from ._common import Reconciler, fail


class EnvironmentReconciler(Reconciler):
    plural = "environments"

    def reconcile(self, spec, meta, status, patch, name, namespace, **_) -> None:
        generation = meta.get("generation", 0)
        state.provider()

        client_ref = (spec.get("clientRef") or {}).get("name")

        if not client_ref:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "clientRef.name is required",
                generation,
            )

        try:
            client_obj = resolve.get_client(client_ref, namespace)
        except resolve.NotFound as exc:
            fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)

        client_slug = (client_obj.get("spec") or {}).get("slug") or client_ref
        environment_class = spec.get("class") or resolve.CLASS_DEVELOPMENT
        env_namespace = resolve.namespace_name(client_slug, name)

        labels = {
            "app.kubernetes.io/managed-by": self.MANAGED_BY,
            "platform.adomi.io/client": client_slug,
            "platform.adomi.io/environment": name,
            "platform.adomi.io/class": environment_class,
        }

        try:
            namespaces.Namespace(env_namespace, labels).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"ensuring namespace: {exc}",
                generation,
            )

        patch.status["namespace"] = env_namespace

        conditions.mark_ready(
            patch,
            status,
            f"Environment {name!r} ready ({env_namespace})",
            generation,
        )

    def finalize(self, status, name, logger, **_) -> None:
        """Delete the environment namespace (cascades remaining Application resources)."""
        env_namespace = status.get("namespace")

        if env_namespace:
            try:
                namespaces.Namespace(env_namespace).delete()
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed deleting namespace {env_namespace!r} during finalize: {exc}")


_reconciler = EnvironmentReconciler()


@kopf.on.create(
    EnvironmentReconciler.GROUP, EnvironmentReconciler.VERSION, EnvironmentReconciler.plural
)
@kopf.on.update(
    EnvironmentReconciler.GROUP, EnvironmentReconciler.VERSION, EnvironmentReconciler.plural
)
@kopf.on.resume(
    EnvironmentReconciler.GROUP, EnvironmentReconciler.VERSION, EnvironmentReconciler.plural
)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)


@kopf.on.delete(
    EnvironmentReconciler.GROUP, EnvironmentReconciler.VERSION, EnvironmentReconciler.plural
)
def finalize(**kwargs) -> None:
    return _reconciler.finalize(**kwargs)
