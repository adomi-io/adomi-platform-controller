"""Tests for the Ingress resource."""

from __future__ import annotations

from adomi_platform_controller.ingress import IngressRoute


def _manifest(**overrides) -> dict:
    base = dict(
        name="gh-acme-erp",
        namespace="argo",
        host="hooks.example.com",
        path="/acme-erp",
        service_name="gh-acme-erp-eventsource-svc",
        service_port=12000,
        tls_secret_name="gh-acme-erp-tls",
        cluster_issuer="letsencrypt-prod",
    )
    base.update(overrides)

    return IngressRoute(**base).manifest()


def test_manifest_shape():
    obj = _manifest()
    assert obj["apiVersion"] == "networking.k8s.io/v1"
    assert obj["kind"] == "Ingress"
    assert obj["spec"]["ingressClassName"] == "traefik"
    rule = obj["spec"]["rules"][0]
    assert rule["host"] == "hooks.example.com"
    p = rule["http"]["paths"][0]
    assert p["path"] == "/acme-erp"
    assert p["backend"]["service"]["name"] == "gh-acme-erp-eventsource-svc"
    assert p["backend"]["service"]["port"]["number"] == 12000


def test_manifest_tls_and_issuer():
    obj = _manifest()
    assert obj["spec"]["tls"] == [{"hosts": ["hooks.example.com"], "secretName": "gh-acme-erp-tls"}]
    assert obj["metadata"]["annotations"]["cert-manager.io/cluster-issuer"] == "letsencrypt-prod"


def test_manifest_without_tls():
    obj = _manifest(tls_secret_name="", cluster_issuer="")
    assert "tls" not in obj["spec"]
    assert "annotations" not in obj["metadata"]
