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
    # When set, deliver these keys instead of the OAuth client-id/client-secret pair.
    # Maps each target Secret data key to the OpenBao property to read for it (e.g.
    # {"password": "password"} for a database role credential).
    data_map: dict[str, str] = field(default_factory=dict)
    refresh_interval: str = "1h"
    labels: dict[str, str] = field(default_factory=dict)
    owner_references: list[dict] = field(default_factory=list)

    def _data(self) -> list[dict]:
        """The ExternalSecret ``data`` entries (custom data_map, else OAuth pair)."""
        if self.data_map:
            return [
                {"secretKey": key, "remoteRef": {"key": self.remote_path, "property": prop}}
                for key, prop in self.data_map.items()
            ]

        return [
            {
                "secretKey": self.client_id_key,
                "remoteRef": {"key": self.remote_path, "property": "client-id"},
            },
            {
                "secretKey": self.client_secret_key,
                "remoteRef": {"key": self.remote_path, "property": "client-secret"},
            },
        ]

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
                "data": self._data(),
            },
        }
