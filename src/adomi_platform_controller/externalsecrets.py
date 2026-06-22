"""External Secrets Operator ExternalSecret resource.

Copies credentials out of OpenBao (via the shared ClusterSecretStore) into an
application's namespace. We talk to the ESO CRD with the dynamic CustomObjectsApi so
the controller does not need a typed ESO client; the CRDs are installed in-cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .kube import CustomResource


@dataclass
class ExternalSecret(CustomResource):
    """A credential-delivery ExternalSecret.

    The remote properties ``client-id``/``client-secret`` match what the
    SSOApplication reconciler writes to OpenBao.
    """

    group = "external-secrets.io"
    version = "v1"
    plural = "externalsecrets"

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

    def manifest(self) -> dict:
        secret_name = self.secret_name or self.name

        metadata: dict = {"name": self.name, "namespace": self.namespace}

        if self.labels:
            metadata["labels"] = self.labels

        if self.owner_references:
            metadata["ownerReferences"] = self.owner_references

        return {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "ExternalSecret",
            "metadata": metadata,
            "spec": {
                "refreshInterval": self.refresh_interval,
                "secretStoreRef": {"kind": "ClusterSecretStore", "name": self.store_name},
                "target": {"name": secret_name, "creationPolicy": "Owner"},
                "data": [
                    {
                        "secretKey": self.client_id_key,
                        "remoteRef": {"key": self.remote_path, "property": "client-id"},
                    },
                    {
                        "secretKey": self.client_secret_key,
                        "remoteRef": {"key": self.remote_path, "property": "client-secret"},
                    },
                ],
            },
        }
