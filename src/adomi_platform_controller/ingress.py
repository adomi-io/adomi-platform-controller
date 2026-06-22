"""Ingress resource (used to expose webhook endpoints).

The preview pipeline exposes each repository's Argo Events github EventSource webhook
service publicly at ``hooks.<domain>/<path>`` so GitHub can reach it, with cert-manager
issuing TLS. We use the typed NetworkingV1Api (Ingress is a stable core API).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from .kube import TypedResource


@dataclass
class IngressRoute(TypedResource):
    """An Ingress routing one host+path to a Service port."""

    name: str
    namespace: str
    host: str
    path: str
    service_name: str
    service_port: int
    ingress_class_name: str = "traefik"
    tls_secret_name: str = ""  # when set, a TLS block for host -> this secret
    cluster_issuer: str = ""  # cert-manager.io/cluster-issuer annotation
    annotations: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def _api() -> client.NetworkingV1Api:
        return client.NetworkingV1Api()

    def manifest(self) -> dict:
        annotations = dict(self.annotations)

        if self.cluster_issuer:
            annotations["cert-manager.io/cluster-issuer"] = self.cluster_issuer

        metadata: dict = {"name": self.name, "namespace": self.namespace}

        if annotations:
            metadata["annotations"] = annotations

        if self.labels:
            metadata["labels"] = self.labels

        spec: dict = {
            "ingressClassName": self.ingress_class_name,
            "rules": [
                {
                    "host": self.host,
                    "http": {
                        "paths": [
                            {
                                "path": self.path,
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": self.service_name,
                                        "port": {"number": self.service_port},
                                    },
                                },
                            },
                        ],
                    },
                },
            ],
        }

        if self.tls_secret_name:
            spec["tls"] = [{"hosts": [self.host], "secretName": self.tls_secret_name}]

        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": metadata,
            "spec": spec,
        }

    def _read(self):
        return self._api().read_namespaced_ingress(self.name, self.namespace)

    def _create(self):
        return self._api().create_namespaced_ingress(self.namespace, self.manifest())

    def _patch(self):
        return self._api().patch_namespaced_ingress(self.name, self.namespace, self.manifest())

    @classmethod
    def delete(cls, name: str, namespace: str) -> None:
        """Delete the Ingress by name (no-op if already gone)."""
        try:
            cls._api().delete_namespaced_ingress(name, namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
