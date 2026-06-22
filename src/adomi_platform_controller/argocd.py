"""Builds and applies Argo CD Application objects.

The Application engine owns *intent*: it computes the Helm values for an
environment and hands the actual workload (Deployment/Service/Ingress/PVC/...) to
Argo CD by creating an Application that points at the Odoo Helm chart. Argo CD then
owns rendering, drift detection, sync, health, retries and history.

We talk to the Argo CD CRD with the dynamic CustomObjectsApi so the controller does
not need a typed client; Argo CD installs the CRDs in-cluster. Applications live in
the Argo CD namespace (not the environment namespace).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

GROUP = "argoproj.io"
VERSION = "v1alpha1"
PLURAL = "applications"

# Argo CD's own finalizer: deleting the Application cascades to (prunes) the
# resources it manages instead of orphaning them.
RESOURCES_FINALIZER = "resources-finalizer.argocd.argoproj.io"

# In-cluster destination; every platform Application deploys to the local cluster.
IN_CLUSTER_SERVER = "https://kubernetes.default.svc"


@dataclass
class Spec:
    """Describes an Argo CD Application that deploys a Helm chart.

    The chart source is either a git repo subdirectory (``path``) or a Helm-repo
    chart (``chart``); set one. ``target_revision`` is the branch/tag (git) or the
    chart version (Helm repo).
    """

    name: str
    namespace: str  # the Argo CD namespace the Application lives in
    repo_url: str  # git repo URL, or Helm repo URL
    target_revision: str  # branch / tag (git) or chart version (helm repo)
    dest_namespace: str  # namespace the workload is deployed into
    path: str = ""  # chart subdirectory (git source)
    chart: str = ""  # chart name (Helm repo source)
    values: dict = field(default_factory=dict)  # helm.valuesObject
    project: str = "default"
    labels: dict[str, str] = field(default_factory=dict)


def build(s: Spec) -> dict:
    """Build the Application object for the spec."""
    metadata: dict = {
        "name": s.name,
        "namespace": s.namespace,
        # The finalizer makes deletion prune the managed workload.
        "finalizers": [RESOURCES_FINALIZER],
    }

    if s.labels:
        metadata["labels"] = s.labels

    source: dict = {
        "repoURL": s.repo_url,
        "targetRevision": s.target_revision,
        "helm": {"valuesObject": s.values},
    }

    if s.chart:
        source["chart"] = s.chart
    else:
        source["path"] = s.path

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "Application",
        "metadata": metadata,
        "spec": {
            "project": s.project,
            "source": source,
            "destination": {
                "server": IN_CLUSTER_SERVER,
                "namespace": s.dest_namespace,
            },
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"],
            },
        },
    }


def apply(s: Spec) -> None:
    """Create or update the Application described by spec (idempotent)."""
    api = client.CustomObjectsApi()
    desired = build(s)

    try:
        existing = api.get_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, s.name)
    except ApiException as exc:
        if exc.status != 404:
            raise

        api.create_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, desired)

        return

    # Preserve Argo CD's own finalizer if something else added more.
    finalizers = set(existing.get("metadata", {}).get("finalizers") or [])
    finalizers.add(RESOURCES_FINALIZER)

    desired["metadata"]["finalizers"] = sorted(finalizers)
    desired["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]

    api.replace_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, s.name, desired)


def delete(name: str, namespace: str) -> None:
    """Delete the Application (no-op if already gone).

    Argo CD's finalizer prunes the managed workload before the Application object
    is removed.
    """
    api = client.CustomObjectsApi()

    try:
        api.delete_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name)
    except ApiException as exc:
        if exc.status != 404:
            raise
