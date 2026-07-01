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
    # Extra non-secret keys to write into the SAME target Secret (literals, e.g. a
    # database host/port/user/dbname). Delivered via the ESO target template so a
    # consumer can wire every connection field from one Secret — the fetched secret
    # values are carried through automatically.
    template_data: dict[str, str] = field(default_factory=dict)
    refresh_interval: str = "1h"
    labels: dict[str, str] = field(default_factory=dict)
    owner_references: list[dict] = field(default_factory=list)

    def _fetched_keys(self) -> list[str]:
        if self.data_map:
            return list(self.data_map.keys())
        return [self.client_id_key, self.client_secret_key]

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

        target: dict = {"name": secret_name, "creationPolicy": "Owner"}

        if self.template_data:
            # With a template the rendered output IS the Secret, so carry the fetched
            # keys through alongside the static metadata. Use `index` (not `.<key>`) so
            # hyphenated keys like client-id work — Go templates read `.client-id` as a
            # subtraction.
            template = dict(self.template_data)
            for key in self._fetched_keys():
                template.setdefault(key, '{{ index . "%s" }}' % key)
            target["template"] = {"engine": "v2", "data": template}

        return {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "ExternalSecret",
            "metadata": metadata,
            "spec": {
                "refreshInterval": self.refresh_interval,
                "secretStoreRef": {"kind": "ClusterSecretStore", "name": self.store_name},
                "target": target,
                "data": self._data(),
            },
        }
