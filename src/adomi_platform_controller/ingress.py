"""Builds and applies Ingress objects (used to expose webhook endpoints).

The preview pipeline exposes each repository's Argo Events github EventSource
webhook service publicly at ``hooks.<domain>/<path>`` so GitHub can reach it, with
cert-manager issuing TLS. We use the typed NetworkingV1Api (Ingress is a stable
core API).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException


@dataclass
class Spec:
    """Describes an Ingress routing one host+path to a Service port."""

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


def build(s: Spec) -> dict:
    """Build the Ingress object for the spec."""
    annotations = dict(s.annotations)

    if s.cluster_issuer:
        annotations["cert-manager.io/cluster-issuer"] = s.cluster_issuer

    metadata: dict = {"name": s.name, "namespace": s.namespace}

    if annotations:
        metadata["annotations"] = annotations

    if s.labels:
        metadata["labels"] = s.labels

    spec: dict = {
        "ingressClassName": s.ingress_class_name,
        "rules": [
            {
                "host": s.host,
                "http": {
                    "paths": [
                        {
                            "path": s.path,
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": s.service_name,
                                    "port": {"number": s.service_port},
                                },
                            },
                        },
                    ],
                },
            },
        ],
    }

    if s.tls_secret_name:
        spec["tls"] = [{"hosts": [s.host], "secretName": s.tls_secret_name}]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": metadata,
        "spec": spec,
    }


def apply(s: Spec) -> None:
    """Create or update the Ingress described by spec (idempotent)."""
    api = client.NetworkingV1Api()
    desired = build(s)

    try:
        api.read_namespaced_ingress(s.name, s.namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise

        api.create_namespaced_ingress(s.namespace, desired)

        return

    api.patch_namespaced_ingress(s.name, s.namespace, desired)


def delete(name: str, namespace: str) -> None:
    """Delete the Ingress (no-op if already gone)."""
    api = client.NetworkingV1Api()

    try:
        api.delete_namespaced_ingress(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
