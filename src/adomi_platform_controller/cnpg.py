"""Builds and applies CloudNativePG Cluster objects.

For in-cluster environments (dev / PDI / preview) the Application engine
provisions a CloudNativePG Cluster per environment. CNPG creates, by convention,
a ``<cluster>-rw`` Service pointing at the primary and a ``<cluster>-app`` Secret
holding the application role's credentials (keys ``username``/``password``/...).
The reconciler wires the Odoo chart at the ``-rw`` host and the ``password`` key of
the ``-app`` secret.

We talk to the CNPG CRD with the dynamic CustomObjectsApi; CNPG installs the CRDs
in-cluster (provided by the kubernetes-provisioner).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

GROUP = "postgresql.cnpg.io"
VERSION = "v1"
PLURAL = "clusters"

# CNPG's conventional generated names, derived from the Cluster name.
RW_SERVICE_SUFFIX = "-rw"  # read-write Service (the primary)
APP_SECRET_SUFFIX = "-app"  # Secret with the application role credentials
APP_SECRET_PASSWORD_KEY = "password"  # key within the -app secret


@dataclass
class Spec:
    """Describes a CloudNativePG Cluster for one Odoo environment."""

    name: str
    namespace: str
    instances: int = 1
    storage_size: str = "10Gi"
    storage_class: str = ""  # empty = cluster default StorageClass
    database: str = "odoo"  # bootstrapped application database
    owner: str = "odoo"  # bootstrapped application role
    image_name: str = ""  # empty = CNPG operator default
    labels: dict[str, str] = field(default_factory=dict)
    owner_references: list[dict] = field(default_factory=list)


def rw_host(cluster_name: str) -> str:
    """The read-write Service host CNPG creates for the cluster's primary."""
    return f"{cluster_name}{RW_SERVICE_SUFFIX}"


def app_secret_name(cluster_name: str) -> str:
    """The Secret CNPG generates holding the application role's credentials."""
    return f"{cluster_name}{APP_SECRET_SUFFIX}"


def build(s: Spec) -> dict:
    """Build the CNPG Cluster object for the spec."""
    metadata: dict = {"name": s.name, "namespace": s.namespace}
    if s.labels:
        metadata["labels"] = s.labels
    if s.owner_references:
        metadata["ownerReferences"] = s.owner_references

    storage: dict = {"size": s.storage_size}
    if s.storage_class:
        storage["storageClass"] = s.storage_class

    spec: dict = {
        "instances": s.instances,
        "storage": storage,
        "bootstrap": {"initdb": {"database": s.database, "owner": s.owner}},
    }
    if s.image_name:
        spec["imageName"] = s.image_name

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "Cluster",
        "metadata": metadata,
        "spec": spec,
    }


def apply(s: Spec) -> None:
    """Create or update the Cluster described by spec (idempotent)."""
    api = client.CustomObjectsApi()
    desired = build(s)

    try:
        api.get_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, s.name)
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, desired)
        return

    api.patch_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, s.name, desired)


def delete(name: str, namespace: str) -> None:
    """Delete the Cluster (no-op if already gone)."""
    api = client.CustomObjectsApi()
    try:
        api.delete_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name)
    except ApiException as exc:
        if exc.status != 404:
            raise
