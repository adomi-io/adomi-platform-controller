"""Ensures and deletes the per-environment namespaces the controller owns.

Each Workspace gets its own namespace so an environment's applications, their databases and
its SSO/ExternalSecret resources can be torn down together by deleting one
namespace (important for ephemeral preview environments).
"""

from __future__ import annotations

from kubernetes import client
from kubernetes.client.exceptions import ApiException


def ensure(name: str, labels: dict[str, str] | None = None) -> None:
    """Create the namespace if it does not exist; patch labels if it does."""
    api = client.CoreV1Api()
    body = client.V1Namespace(metadata=client.V1ObjectMeta(name=name, labels=labels or {}))

    try:
        api.read_namespace(name)
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_namespace(body)
        return

    if labels:
        api.patch_namespace(
            name,
            {"metadata": {"labels": labels}},
        )


def delete(name: str) -> None:
    """Delete the namespace (no-op if already gone or already terminating)."""
    api = client.CoreV1Api()
    try:
        api.delete_namespace(name)
    except ApiException as exc:
        if exc.status not in (404, 409):
            raise
