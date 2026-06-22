"""Argo Workflow resource, submitted from the shipped build WorkflowTemplate.

When an Application builds from source, the controller submits a Workflow that
references a WorkflowTemplate (installed by the chart into the ``argo`` namespace)
with per-environment parameters, then polls the Workflow's phase and gates the deploy
on a successful build. A submitted Workflow is immutable, so ``apply`` only creates it
if absent (it never patches a running build).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .kube import CustomResource

# Argo Workflow phases (status.phase).
PHASE_PENDING = "Pending"
PHASE_RUNNING = "Running"
PHASE_SUCCEEDED = "Succeeded"
PHASE_FAILED = "Failed"
PHASE_ERROR = "Error"


def phase(obj: dict | None) -> str:
    """The Workflow's phase, or "" when unknown / not yet reported."""
    if not obj:
        return ""

    return (obj.get("status") or {}).get("phase") or ""


@dataclass
class Workflow(CustomResource):
    """A Workflow submitted from a WorkflowTemplate."""

    group = "argoproj.io"
    version = "v1alpha1"
    plural = "workflows"

    name: str
    namespace: str  # the Argo namespace (e.g. "argo")
    workflow_template_ref: str  # the WorkflowTemplate to run
    parameters: dict[str, str] = field(default_factory=dict)
    service_account: str = ""  # ServiceAccount the build runs as
    labels: dict[str, str] = field(default_factory=dict)

    def manifest(self) -> dict:
        metadata: dict = {"name": self.name, "namespace": self.namespace}

        if self.labels:
            metadata["labels"] = self.labels

        spec: dict = {
            "workflowTemplateRef": {"name": self.workflow_template_ref},
            "arguments": {
                "parameters": [
                    {"name": k, "value": v} for k, v in sorted(self.parameters.items())
                ],
            },
        }

        if self.service_account:
            spec["serviceAccountName"] = self.service_account

        return {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "Workflow",
            "metadata": metadata,
            "spec": spec,
        }

    def apply(self) -> dict | None:
        """Create the Workflow if absent (submitted Workflows are immutable)."""
        if self.get() is not None:
            return None  # already submitted; do not patch a running build

        return self.create()
