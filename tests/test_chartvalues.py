"""Tests for the explicit-intent -> per-app chart value mapping."""

from __future__ import annotations

from adomi_platform_controller.chartvalues import build_chart_values


def test_build_chart_values_full():
    v = build_chart_values(
        client_slug="acme",
        replicas=2,
        image="ghcr.io/adomi-io/odoo:19.0",
        ingress_host="odoo-prod-acme.example.com",
        ingress_class_name="traefik",
        ingress_tls=[{"secretName": "odoo-tls", "hosts": ["odoo-prod-acme.example.com"]}],
        ingress_annotations={"traefik.ingress.kubernetes.io/router.middlewares": "x"},
        databases=[
            {"name": "main", "server": "acme-prod-db", "credentials": {"secret": "odoo-main-db"}}
        ],
        sso=[{"name": "web", "protocol": "oauth2", "credentials": {"secret": "odoo-oidc"}}],
        env=[{"name": "ODOO_DB_HOST", "value": "main-rw.acme-prod.svc.cluster.local"}],
    )

    assert v["platform"] == {"client": "acme"}
    assert v["replicaCount"] == 2
    assert v["image"] == {"repository": "ghcr.io/adomi-io/odoo", "tag": "19.0"}
    assert v["databases"][0]["server"] == "acme-prod-db"
    assert v["databases"][0]["credentials"]["secret"] == "odoo-main-db"
    assert v["sso"][0]["credentials"]["secret"] == "odoo-oidc"
    assert v["env"][0]["name"] == "ODOO_DB_HOST"
    assert v["ingress"]["enabled"] is True
    assert v["ingress"]["hosts"][0]["host"] == "odoo-prod-acme.example.com"
    assert v["ingress"]["tls"][0]["secretName"] == "odoo-tls"
    assert "middlewares" in next(iter(v["ingress"]["annotations"]))


def test_build_chart_values_minimal_omits_image_and_ingress():
    v = build_chart_values(
        client_slug="acme",
        replicas=1,
        image="",
        ingress_host="",
        ingress_class_name="",
        ingress_tls=[],
        ingress_annotations=None,
        databases=[],
        sso=[],
        env=[],
    )

    assert "image" not in v
    assert "ingress" not in v
    assert v["databases"] == []
    assert v["sso"] == []
    assert v["env"] == []


def test_build_chart_values_image_without_tag():
    v = build_chart_values(
        client_slug="acme",
        replicas=1,
        image="nginx",
        ingress_host="",
        ingress_class_name="",
        ingress_tls=[],
        ingress_annotations=None,
        databases=[],
        sso=[],
        env=[],
    )

    assert v["image"] == {"repository": "nginx"}
