"""Tests for the Ingress builder."""

from __future__ import annotations

from adomi_platform_controller import ingress


def _build(**overrides) -> dict:
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
    return ingress.build(ingress.Spec(**base))


def test_build_shape():
    obj = _build()
    assert obj["apiVersion"] == "networking.k8s.io/v1"
    assert obj["kind"] == "Ingress"
    assert obj["spec"]["ingressClassName"] == "traefik"
    rule = obj["spec"]["rules"][0]
    assert rule["host"] == "hooks.example.com"
    p = rule["http"]["paths"][0]
    assert p["path"] == "/acme-erp"
    assert p["backend"]["service"]["name"] == "gh-acme-erp-eventsource-svc"
    assert p["backend"]["service"]["port"]["number"] == 12000


def test_build_tls_and_issuer():
    obj = _build()
    assert obj["spec"]["tls"] == [{"hosts": ["hooks.example.com"], "secretName": "gh-acme-erp-tls"}]
    assert obj["metadata"]["annotations"]["cert-manager.io/cluster-issuer"] == "letsencrypt-prod"


def test_build_without_tls():
    obj = _build(tls_secret_name="", cluster_issuer="")
    assert "tls" not in obj["spec"]
    assert "annotations" not in obj["metadata"]
