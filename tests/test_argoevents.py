"""Tests for the Argo Events EventSource/Sensor builders and naming."""

from __future__ import annotations

from adomi_platform_controller import argoevents


def test_naming():
    assert argoevents.eventsource_name("Acme", "Odoo_ERP") == "gh-acme-odoo-erp"
    assert argoevents.webhook_path("acme", "erp") == "/acme-erp"
    assert argoevents.service_name("gh-acme-erp") == "gh-acme-erp-eventsource-svc"


def test_eventsource_shape():
    es = argoevents.build_eventsource(
        argoevents.EventSourceSpec(
            name="gh-acme-erp",
            namespace="argo",
            owner="acme",
            repo="erp",
            webhook_url="https://hooks.example.com",
            webhook_path="/acme-erp",
            token_secret="gh-acme-erp-token",
            webhook_secret="gh-acme-erp-webhook",
        )
    )
    gh = es["spec"]["github"][argoevents.EVENT_KEY]
    assert gh["repositories"] == [{"owner": "acme", "names": ["erp"]}]
    assert gh["events"] == ["pull_request"]
    assert gh["webhook"]["url"] == "https://hooks.example.com"
    assert gh["apiToken"] == {"name": "gh-acme-erp-token", "key": "token"}


def _sensor() -> dict:
    return argoevents.build_sensor(
        argoevents.SensorSpec(
            name="gh-acme-erp",
            namespace="argo",
            eventsource_name="gh-acme-erp",
            service_account="odoo-previews",
            owner="acme",
            repo="erp",
            mgmt_namespace="adomi-system",
            client_ref="acme",
            application_type="odoo",
            repository_ref="erp-src",
        )
    )


def test_sensor_dependencies_and_triggers():
    s = _sensor()["spec"]
    assert s["template"]["serviceAccountName"] == "odoo-previews"
    assert {d["name"] for d in s["dependencies"]} == {"pr-open", "pr-sync", "pr-close"}
    triggers = {t["template"]["name"]: t["template"] for t in s["triggers"]}
    assert set(triggers) == {
        "create-environment",
        "create-app",
        "sync-app",
        "delete-app",
        "delete-environment",
    }
    assert triggers["create-app"]["k8s"]["operation"] == "create"
    assert triggers["sync-app"]["k8s"]["operation"] == "patch"
    assert triggers["delete-environment"]["k8s"]["operation"] == "delete"


def test_sensor_create_resources():
    triggers = {t["template"]["name"]: t["template"] for t in _sensor()["spec"]["triggers"]}

    ws = triggers["create-environment"]["k8s"]["source"]["resource"]
    assert ws["kind"] == "Environment"
    assert ws["spec"]["clientRef"] == {"name": "acme"}
    assert ws["spec"]["class"] == "preview"

    app = triggers["create-app"]["k8s"]["source"]["resource"]
    assert app["kind"] == "Application"
    assert app["spec"]["type"] == "odoo"
    assert app["spec"]["source"]["repositoryRef"] == {"name": "erp-src"}
    assert app["metadata"]["annotations"][argoevents.ANN_REPO] == "acme/erp"

    dests = {p["dest"] for p in triggers["create-app"]["k8s"]["parameters"]}
    assert "metadata.name" in dests
    assert "spec.environmentRef.name" in dests
    assert "spec.source.ref" in dests
