"""WorkspaceReconciler.

A Workspace is a named environment for a Client (production / dev / pdi / preview /
test). It owns a namespace (``<client>-<workspace>``) that its Applications deploy
into. The reconciler resolves the owning Client and ensures the namespace.
"""

from __future__ import annotations

import kopf

from .. import conditions, namespaces, resolve, state
from ._common import Reconciler, fail


class WorkspaceReconciler(Reconciler):
    plural = "workspaces"

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
        workspace_class = spec.get("class") or resolve.CLASS_DEVELOPMENT
        ws_namespace = resolve.namespace_name(client_slug, name)

        labels = {
            "app.kubernetes.io/managed-by": self.MANAGED_BY,
            "platform.adomi.io/client": client_slug,
            "platform.adomi.io/workspace": name,
            "platform.adomi.io/class": workspace_class,
        }

        try:
            namespaces.Namespace(ws_namespace, labels).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"ensuring namespace: {exc}",
                generation,
            )

        patch.status["namespace"] = ws_namespace

        conditions.mark_ready(
            patch,
            status,
            f"Workspace {name!r} ready ({ws_namespace})",
            generation,
        )

    def finalize(self, status, name, logger, **_) -> None:
        """Delete the workspace namespace (cascades remaining Application resources)."""
        ws_namespace = status.get("namespace")

        if ws_namespace:
            try:
                namespaces.Namespace(ws_namespace).delete()
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed deleting namespace {ws_namespace!r} during finalize: {exc}")


_reconciler = WorkspaceReconciler()


@kopf.on.create(WorkspaceReconciler.GROUP, WorkspaceReconciler.VERSION, WorkspaceReconciler.plural)
@kopf.on.update(WorkspaceReconciler.GROUP, WorkspaceReconciler.VERSION, WorkspaceReconciler.plural)
@kopf.on.resume(WorkspaceReconciler.GROUP, WorkspaceReconciler.VERSION, WorkspaceReconciler.plural)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)


@kopf.on.delete(WorkspaceReconciler.GROUP, WorkspaceReconciler.VERSION, WorkspaceReconciler.plural)
def finalize(**kwargs) -> None:
    return _reconciler.finalize(**kwargs)
