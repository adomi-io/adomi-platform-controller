"""Canonical platform resource schema, shared by the API and the controller.

This is the single source of truth for the ``platform.adomi.io`` resources the
platform exposes. The API and the operator speak the **same object language** — the
CRD kinds and plurals (Client, Workspace, Application, ...) — so there is no second
vocabulary to translate.

Only **customer-owned, namespaced** kinds live here — the resources a tenant repo may
contain and that the API writes to git. Cluster-scoped / platform-owned resources
(Organization, the base ApplicationType catalog) and ephemeral resources (PR
previews, which the controller creates in-cluster directly) are intentionally absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"

# Default per-customer namespace prefix (a Client's CRs land in <prefix><client>).
# Must match the provisioner's tenants.tenantNamespacePrefix and the Odoo addon.
DEFAULT_TENANT_NAMESPACE_PREFIX = "adomi-tenant-"

MANAGED_BY = "adomi-platform-api"


@dataclass(frozen=True)
class ResourceType:
    """A platform CRD: its plural, kind, and (for nested resources) its parent plural."""

    plural: str  # CRD plural, e.g. "workspaces", "applications"
    kind: str  # CRD kind, e.g. "Workspace", "Application"
    parent: str | None = None  # plural this nests under in the URL (applications -> workspaces)


# The catalog of customer-owned resources, in dependency order. A Client owns its
# tenant repo; everything else belongs to a Client.
RESOURCE_TYPES: tuple[ResourceType, ...] = (
    ResourceType(plural="clients", kind="Client"),
    ResourceType(plural="domains", kind="Domain"),
    ResourceType(plural="databases", kind="Database"),
    ResourceType(plural="workspaces", kind="Workspace"),
    ResourceType(plural="applications", kind="Application", parent="workspaces"),
    ResourceType(plural="gitrepositories", kind="GitRepository"),
    ResourceType(plural="snapshots", kind="Snapshot"),
)

BY_PLURAL: dict[str, ResourceType] = {r.plural: r for r in RESOURCE_TYPES}

_DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class SchemaError(ValueError):
    """Invalid resource name or unknown resource type."""


def validate_name(value: str, what: str = "name") -> str:
    """Validate a DNS-1123 label (<=63 chars) used as a CR metadata.name."""
    if not value or len(value) > 63 or not _DNS1123.match(value):
        raise SchemaError(f"invalid {what} {value!r} (must be a DNS-1123 label, <=63 chars)")

    return value


def resource_for_plural(plural: str) -> ResourceType:
    if plural not in BY_PLURAL:
        raise SchemaError(f"unknown resource plural {plural!r}")

    return BY_PLURAL[plural]


def tenant_namespace(client: str, prefix: str = DEFAULT_TENANT_NAMESPACE_PREFIX) -> str:
    """The namespace a Client's committed CRs live in."""
    return f"{prefix}{client}"


def repo_path(plural: str, name: str) -> str:
    """Where a resource's manifest lives in the Client's tenant repo."""
    return f"{plural}/{name}.yaml"


def build_manifest(
    plural: str,
    name: str,
    spec: dict,
    *,
    client: str,
    namespace_prefix: str = DEFAULT_TENANT_NAMESPACE_PREFIX,
    managed_by: str = MANAGED_BY,
    labels: dict[str, str] | None = None,
) -> dict:
    """Wrap a resource ``spec`` into the full custom-resource object."""
    rt = resource_for_plural(plural)

    meta_labels = {
        "app.kubernetes.io/managed-by": managed_by,
        "platform.adomi.io/client": client,
    }
    if labels:
        meta_labels.update(labels)

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": rt.kind,
        "metadata": {
            "name": name,
            "namespace": tenant_namespace(client, namespace_prefix),
            "labels": meta_labels,
        },
        "spec": spec or {},
    }
