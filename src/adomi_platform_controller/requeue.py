"""Fan-out: nudge dependent Applications when a scope they inherit from changes.

Kopf is event-driven, so an Application only re-renders when the Application
object itself changes. But much of its effective configuration lives on OTHER
objects — variables on the Organization / Client / Environment, the catalog
entry's chart + defaultValues, a Domain's fqdn — and editing those used to
leave running apps stale until their next unrelated update.

The fix is the standard watch-the-parents pattern: a parent's reconciler stamps
every dependent Application with a revision annotation
(``platform.adomi.io/config-revision: <kind>/<name>@<generation>``). That is an
essence change kopf treats like any other edit, so each app re-renders through
its normal reconcile. The stamp is skipped when the annotation already carries
the revision, which makes the fan-out idempotent: controller restarts (resume)
and status-only updates re-run the parent handler with an unchanged generation
and touch nothing.
"""

from __future__ import annotations

from kubernetes import client

from . import resolve

ANNOTATION = "platform.adomi.io/config-revision"


def revision(kind: str, name: str, generation) -> str:
    """The annotation value for a parent at a spec generation."""
    return f"{kind}/{name}@{generation or 0}"


def requeue_applications(
    rev: str,
    *,
    namespace: str | None = None,
    predicate=None,
    logger=None,
) -> int:
    """Stamp Applications (one namespace, or cluster-wide) with ``rev``.

    ``predicate(item) -> bool`` narrows the fan-out (e.g. apps of one type or
    referencing one domain). Returns how many apps were actually stamped.
    Failures are logged and swallowed: requeueing is an accelerator, never a
    reason to fail the parent's own reconcile.
    """
    api = client.CustomObjectsApi()

    try:
        if namespace:
            listing = api.list_namespaced_custom_object(
                resolve.GROUP, resolve.VERSION, namespace, resolve.PLURAL_APPLICATIONS
            )
        else:
            listing = api.list_cluster_custom_object(
                resolve.GROUP, resolve.VERSION, resolve.PLURAL_APPLICATIONS
            )
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warning(f"Requeue skipped, listing applications failed: {exc}")

        return 0

    count = 0

    for item in listing.get("items") or []:
        meta = item.get("metadata") or {}

        if (meta.get("annotations") or {}).get(ANNOTATION) == rev:
            continue

        if predicate and not predicate(item):
            continue

        try:
            api.patch_namespaced_custom_object(
                resolve.GROUP,
                resolve.VERSION,
                meta.get("namespace"),
                resolve.PLURAL_APPLICATIONS,
                meta.get("name"),
                {"metadata": {"annotations": {ANNOTATION: rev}}},
            )
            count += 1
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.warning(
                    f"Requeue of application {meta.get('namespace')}/{meta.get('name')} "
                    f"failed: {exc}"
                )

    if count and logger:
        logger.info(f"Requeued {count} application(s) for {rev}")

    return count


def app_references_environment(environment_name: str):
    def predicate(item: dict) -> bool:
        spec = item.get("spec") or {}

        return ((spec.get("environmentRef") or {}).get("name") or "") == environment_name

    return predicate


def app_references_type(type_name: str):
    def predicate(item: dict) -> bool:
        return ((item.get("spec") or {}).get("type") or "") == type_name

    return predicate


def app_references_domain(domain_name: str):
    def predicate(item: dict) -> bool:
        spec = item.get("spec") or {}

        return ((spec.get("domainRef") or {}).get("name") or "") == domain_name

    return predicate
