"""Builds and applies External Secrets Operator ExternalSecret objects.

These copy credentials out of OpenBao (via the shared ClusterSecretStore) into an
application's namespace. We talk to the ESO CRD with the dynamic CustomObjectsApi
so the controller does not need a typed ESO client; the CRDs are installed
in-cluster by the platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

GROUP = "external-secrets.io"
VERSION = "v1"
PLURAL = "externalsecrets"


@dataclass
class Spec:
    """Describes a credential-delivery ExternalSecret."""

    name: str
    namespace: str
    store_name: str  # the ClusterSecretStore to read from (e.g. "openbao")
    remote_path: str  # the OpenBao KV path holding the credentials
    secret_name: str = ""  # target Secret name (defaults to name)
    client_id_key: str = "client-id"
    client_secret_key: str = "client-secret"
    refresh_interval: str = "1h"
    labels: dict[str, str] = field(default_factory=dict)
    owner_references: list[dict] = field(default_factory=list)


def build(s: Spec) -> dict:
    """Build the ExternalSecret object for the spec.

    The remote properties ``client-id``/``client-secret`` match what the
    SSOApplication reconciler writes to OpenBao.
    """
    secret_name = s.secret_name or s.name
    metadata: dict = {"name": s.name, "namespace": s.namespace}
    if s.labels:
        metadata["labels"] = s.labels
    if s.owner_references:
        metadata["ownerReferences"] = s.owner_references

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "ExternalSecret",
        "metadata": metadata,
        "spec": {
            "refreshInterval": s.refresh_interval,
            "secretStoreRef": {"kind": "ClusterSecretStore", "name": s.store_name},
            "target": {"name": secret_name, "creationPolicy": "Owner"},
            "data": [
                {
                    "secretKey": s.client_id_key,
                    "remoteRef": {"key": s.remote_path, "property": "client-id"},
                },
                {
                    "secretKey": s.client_secret_key,
                    "remoteRef": {"key": s.remote_path, "property": "client-secret"},
                },
            ],
        },
    }


def apply(s: Spec) -> None:
    """Create or update the ExternalSecret described by spec (idempotent)."""
    api = client.CustomObjectsApi()
    desired = build(s)

    try:
        api.get_namespaced_custom_object(
            GROUP, VERSION, s.namespace, PLURAL, s.name
        )
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_namespaced_custom_object(GROUP, VERSION, s.namespace, PLURAL, desired)
        return

    # Exists: merge-patch the spec, labels, and owner references to match desired.
    api.patch_namespaced_custom_object(
        GROUP, VERSION, s.namespace, PLURAL, s.name, desired
    )