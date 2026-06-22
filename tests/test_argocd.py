"""Tests for the Argo CD Application resource."""

from __future__ import annotations

from adomi_platform_controller import argocd
from adomi_platform_controller.argocd import ArgoApplication


def _app(**overrides) -> ArgoApplication:
    base = dict(
        name="acme-erp-dev",
        namespace="argocd",
        repo_url="https://github.com/adomi-io/adomi-helm.git",
        path="charts/odoo",
        target_revision="master",
        dest_namespace="acme-erp-dev",
        values={"replicaCount": 1},
    )
    base.update(overrides)

    return ArgoApplication(**base)


def test_manifest_shape():
    app = _app().manifest()

    assert app["apiVersion"] == "argoproj.io/v1alpha1"
    assert app["kind"] == "Application"
    assert app["metadata"]["name"] == "acme-erp-dev"
    assert app["metadata"]["namespace"] == "argocd"
    # Deletion must prune the managed workload.
    assert ArgoApplication.RESOURCES_FINALIZER in app["metadata"]["finalizers"]


def test_manifest_source_and_destination():
    spec = _app().manifest()["spec"]

    assert spec["project"] == "default"
    assert spec["source"]["repoURL"] == "https://github.com/adomi-io/adomi-helm.git"
    assert spec["source"]["path"] == "charts/odoo"
    assert spec["source"]["targetRevision"] == "master"
    assert spec["source"]["helm"]["valuesObject"] == {"replicaCount": 1}

    assert spec["destination"]["server"] == argocd.IN_CLUSTER_SERVER
    assert spec["destination"]["namespace"] == "acme-erp-dev"


def test_manifest_sync_policy():
    sync = _app().manifest()["spec"]["syncPolicy"]

    assert sync["automated"] == {"prune": True, "selfHeal": True}
    assert "CreateNamespace=true" in sync["syncOptions"]


def test_project_override():
    app = _app(project="platform").manifest()
    assert app["spec"]["project"] == "platform"
