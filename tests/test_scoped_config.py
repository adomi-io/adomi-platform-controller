"""Scoped Variables + Secrets: merge precedence, secret paths, and delivery shape."""

from __future__ import annotations

from adomi_platform_controller import chartvalues, resolve
from adomi_platform_controller.config import Config
from adomi_platform_controller.externalsecrets import ExternalSecret


def test_merged_env_nearest_scope_wins():
    env = resolve.merged_env(
        org_spec={"variables": [{"name": "TZ", "value": "UTC"}, {"name": "LOG", "value": "info"}]},
        client_spec={"variables": [{"name": "LOG", "value": "warn"}]},
        environment_spec={"variables": [{"name": "TIER", "value": "production"}]},
        app_spec={"variables": [{"name": "TIER", "value": "prod-app"}]},
    )
    as_map = {e["name"]: e["value"] for e in env}
    assert as_map == {"TZ": "UTC", "LOG": "warn", "TIER": "prod-app"}


def test_merged_env_explicit_app_env_beats_variables():
    # spec.env is the app's explicit wiring: it overrides same-named variables and
    # passes through untouched (including valueFrom entries with no plain value).
    ref = {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "erp-db"}}}
    env = resolve.merged_env(
        org_spec={"variables": [{"name": "DB_PASSWORD", "value": "leaked-default"}]},
        client_spec={},
        environment_spec={"variables": [{"name": "PORT", "value": "8080"}]},
        app_spec={"env": [ref]},
    )
    as_map = {e["name"]: e for e in env}
    assert as_map["DB_PASSWORD"] == ref  # verbatim, no plain value
    assert as_map["PORT"]["value"] == "8080"


def test_merged_env_skips_nameless_entries():
    env = resolve.merged_env(
        org_spec={"variables": [{"value": "orphan"}, {"name": "", "value": "blank"}]},
        client_spec=None,
        environment_spec=None,
        app_spec={"env": [{"name": "A", "value": "1"}]},
    )
    assert env == [{"name": "A", "value": "1"}]


def test_scoped_secret_paths_least_to_most_specific():
    paths = resolve.scoped_secret_paths("scoped", "adomi-org", "acme", "production", "erp")
    assert paths == [
        "scoped/org/adomi-org",
        "scoped/clients/acme",
        "scoped/clients/acme/environments/production",
        "scoped/clients/acme/environments/production/applications/erp",
    ]


def test_scoped_secret_paths_without_org():
    paths = resolve.scoped_secret_paths("scoped", "", "acme", "dev", "erp")
    assert paths[0] == "scoped/clients/acme"
    assert len(paths) == 3


def test_compute_fills_env_and_secret_paths():
    eff = resolve.compute(
        Config(base_domain="adomi.io"),
        org_spec={"variables": [{"name": "TZ", "value": "UTC"}]},
        client_name="acme",
        client_spec={},
        environment_name="production",
        environment_spec={"class": "production"},
        app_name="erp",
        app_spec={"env": [{"name": "PORT", "value": "8080"}]},
        type_spec={"chart": {"path": "charts/odoo"}},
        org_name="adomi-org",
    )
    assert {e["name"]: e.get("value") for e in eff.env} == {"TZ": "UTC", "PORT": "8080"}
    assert eff.scoped_secret_paths[0] == "scoped/org/adomi-org"
    assert eff.scoped_secret_paths[-1].endswith("applications/erp")


def test_chart_values_env_from_secret():
    values = chartvalues.build_chart_values(
        client_slug="acme",
        replicas=1,
        image="",
        ingress_host="",
        ingress_class_name="traefik",
        ingress_tls=[],
        ingress_annotations=None,
        databases=[],
        sso=[],
        env=[],
        env_from_secret="erp-scoped-secrets",
    )
    assert values["extraEnvFrom"] == [{"secretRef": {"name": "erp-scoped-secrets"}}]

    bare = chartvalues.build_chart_values(
        client_slug="acme",
        replicas=1,
        image="",
        ingress_host="",
        ingress_class_name="traefik",
        ingress_tls=[],
        ingress_annotations=None,
        databases=[],
        sso=[],
        env=[],
    )
    assert "extraEnvFrom" not in bare


def test_externalsecret_data_from_merge_order():
    manifest = ExternalSecret(
        name="erp-scoped-secrets",
        namespace="acme-production",
        store_name="openbao",
        remote_path="",
        data_from_paths=[
            "scoped/clients/acme",
            "scoped/clients/acme/environments/production",
        ],
        refresh_interval="1m",
    ).manifest()

    spec = manifest["spec"]
    # dataFrom in least->most specific order (ESO: later keys win) and no data list.
    assert spec["dataFrom"] == [
        {"extract": {"key": "scoped/clients/acme"}},
        {"extract": {"key": "scoped/clients/acme/environments/production"}},
    ]
    assert "data" not in spec
    assert spec["refreshInterval"] == "1m"
