"""Builds and submits Argo Workflows from the shipped build WorkflowTemplate.

When an Application builds from source, the controller submits a Workflow that
references the ``odoo-image-build`` WorkflowTemplate (installed by the chart into the
``argo`` namespace) with per-environment parameters. The controller then polls the
Workflow's phase and gates the deploy on a successful build.

A submitted Workflow is immutable, so ``apply`` only creates it if absent (it never
patches a running build). We use the dynamic CustomObjectsApi; Argo Workflows
installs the CRDs in-cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

GROUP = "argoproj.io"
VERSION = "v1alpha1"
PLURAL = "workflows"

# Argo Workflow phases (status.phase).
PHASE_PENDING = "Pending"
PHASE_RUNNING = "Running"
PHASE_SUCCEEDED = "Succeeded"
PHASE_FAILED = "Failed"
PHASE_ERROR = "Error"


@dataclass
class Spec:
    """Describes a Workflow submitted from a WorkflowTemplate."""

    name: str
    namespace: str  # the Argo namespace (e.g. "argo")
    workflow_template_ref: str  # the WorkflowTemplate to run
    parameters: dict[str, str] = field(default_factory=dict)
    service_account: str = ""  # ServiceAccount the build runs as
    labels: dict[str, str] = field(default_factory=dict)


def build(s: Spec) -> dict:
    """Build the Workflow object for the spec."""
    metadata: dict = {"name": s.name, "namespace": s.namespace}

    if s.labels:
        metadata["labels"] = s.labels

    spec: dict = {
        "workflowTemplateRef": {"name": s.workflow_template_ref},
        "arguments": {
            "parameters": [{"name": k, "value": v} for k, v in sorted(s.parameters.items())],
        },
    }

    if s.service_account:
        spec["serviceAccountName"] = s.service_account

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "Workflow",
        "metadata": metadata,
        "spec": spec,
    }


def apply(s: Spec) -> None:
    """Create the Workflow if it does not exist (submitted Workflows are immutable)."""
    api = client.CustomObjectsApi()

    try:
        api.get_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, s.name)
        return  # already submitted; do not patch a running build
    except ApiException as exc:
        if exc.status != 404:
            raise

    api.create_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, build(s))


def get(name: str, namespace: str) -> dict | None:
    """Return the Workflow object, or None if it does not exist."""
    api = client.CustomObjectsApi()

    try:
        return api.get_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name)
    except ApiException as exc:
        if exc.status == 404:
            return None

        raise


def phase(obj: dict | None) -> str:
    """The Workflow's phase, or "" when unknown / not yet reported."""
    if not obj:
        return ""

    return (obj.get("status") or {}).get("phase") or ""


def delete(name: str, namespace: str) -> None:
    """Delete the Workflow (no-op if already gone)."""
    api = client.CustomObjectsApi()

    try:
        api.delete_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name)
    except ApiException as exc:
        if exc.status != 404:
            raise
