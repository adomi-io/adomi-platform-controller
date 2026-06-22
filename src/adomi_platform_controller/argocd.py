"""Argo CD Application resource.

The Application engine owns *intent*: it computes the Helm values for an environment
and hands the actual workload (Deployment/Service/Ingress/PVC/...) to Argo CD by
creating an Application that points at the chart. Argo CD then owns rendering, drift
detection, sync, health, retries and history. Applications live in the Argo CD
namespace (not the environment namespace).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .kube import CustomResource

# In-cluster destination; every platform Application deploys to the local cluster.
IN_CLUSTER_SERVER = "https://kubernetes.default.svc"


@dataclass
class ArgoApplication(CustomResource):
    """An Argo CD Application that deploys a Helm chart.

    The chart source is either a git repo subdirectory (``path``) or a Helm-repo
    chart (``chart``); set one. ``target_revision`` is the branch/tag (git) or the
    chart version (Helm repo).
    """

    group = "argoproj.io"
    version = "v1alpha1"
    plural = "applications"

    # Argo CD's own finalizer: deleting the Application cascades to (prunes) the
    # resources it manages instead of orphaning them.
    RESOURCES_FINALIZER = "resources-finalizer.argocd.argoproj.io"

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

    def manifest(self) -> dict:
        metadata: dict = {
            "name": self.name,
            "namespace": self.namespace,
            # The finalizer makes deletion prune the managed workload.
            "finalizers": [self.RESOURCES_FINALIZER],
        }

        if self.labels:
            metadata["labels"] = self.labels

        source: dict = {
            "repoURL": self.repo_url,
            "targetRevision": self.target_revision,
            "helm": {"valuesObject": self.values},
        }

        if self.chart:
            source["chart"] = self.chart
        else:
            source["path"] = self.path

        return {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "Application",
            "metadata": metadata,
            "spec": {
                "project": self.project,
                "source": source,
                "destination": {
                    "server": IN_CLUSTER_SERVER,
                    "namespace": self.dest_namespace,
                },
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                    "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"],
                },
            },
        }

    def apply(self) -> dict:
        """Create the Application, or replace it preserving Argo CD's finalizer."""
        existing = self.get()
        desired = self.manifest()

        if existing is None:
            return self.create()

        # Preserve Argo CD's own finalizer if something else added more.
        finalizers = set(existing.get("metadata", {}).get("finalizers") or [])
        finalizers.add(self.RESOURCES_FINALIZER)

        desired["metadata"]["finalizers"] = sorted(finalizers)
        desired["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]

        return self.replace(desired)
