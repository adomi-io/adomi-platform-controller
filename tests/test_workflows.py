"""Tests for the Argo Workflow builder and phase helper."""

from __future__ import annotations

from adomi_platform_controller import workflows


def test_build_shape():
    obj = workflows.build(
        workflows.Spec(
            name="build-acme-erp-dev-main",
            namespace="argo",
            workflow_template_ref="odoo-image-build",
            service_account="odoo-build",
            parameters={"repoURL": "https://github.com/acme/erp", "ref": "main"},
        )
    )

    assert obj["apiVersion"] == "argoproj.io/v1alpha1"
    assert obj["kind"] == "Workflow"
    assert obj["metadata"]["name"] == "build-acme-erp-dev-main"
    assert obj["metadata"]["namespace"] == "argo"

    spec = obj["spec"]
    assert spec["workflowTemplateRef"] == {"name": "odoo-image-build"}
    assert spec["serviceAccountName"] == "odoo-build"
    params = {p["name"]: p["value"] for p in spec["arguments"]["parameters"]}
    assert params == {"repoURL": "https://github.com/acme/erp", "ref": "main"}


def test_build_omits_service_account_when_unset():
    obj = workflows.build(workflows.Spec(name="w", namespace="argo", workflow_template_ref="t"))
    assert "serviceAccountName" not in obj["spec"]


def test_phase():
    assert workflows.phase(None) == ""
    assert workflows.phase({}) == ""
    assert workflows.phase({"status": {}}) == ""
    assert workflows.phase({"status": {"phase": "Running"}}) == workflows.PHASE_RUNNING
    assert workflows.phase({"status": {"phase": "Succeeded"}}) == workflows.PHASE_SUCCEEDED
