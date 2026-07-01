"""Tests for the OIDC descriptor the SSOApplication publishes for downstream apps."""

from __future__ import annotations

from adomi_platform_controller import chartvalues, config, externalsecrets, oidc, resolve
from adomi_platform_controller.handlers.application import ApplicationReconciler


def test_descriptor_builds_standard_authentik_endpoints():
    d = oidc.descriptor("https://auth.example.com", "windmill-adomi")

    assert d["issuer"] == "https://auth.example.com/application/o/windmill-adomi/"
    assert d["discovery-url"] == (
        "https://auth.example.com/application/o/windmill-adomi/.well-known/openid-configuration"
    )
    # The three runtime endpoints are shared (not per-app) in Authentik.
    assert d["authorization-endpoint"] == "https://auth.example.com/application/o/authorize/"
    assert d["token-endpoint"] == "https://auth.example.com/application/o/token/"
    assert d["userinfo-endpoint"] == "https://auth.example.com/application/o/userinfo/"
    # JWKS + end-session are per-application.
    assert d["jwks-uri"] == "https://auth.example.com/application/o/windmill-adomi/jwks/"
    assert d["scopes"] == "openid email profile"


def test_descriptor_strips_trailing_slash_and_takes_custom_scopes():
    d = oidc.descriptor("https://auth.example.com/", "app", ["openid", "groups"])
    assert d["issuer"] == "https://auth.example.com/application/o/app/"
    assert d["scopes"] == "openid groups"


def test_resolved_authentik_url_falls_back_to_auth_subdomain():
    assert config.Config(base_domain="example.com").resolved_authentik_url() == (
        "https://auth.example.com"
    )
    # An explicit host wins and a bare host is upgraded to https.
    assert (
        config.Config(
            authentik_public_host="id.example.org", base_domain="example.com"
        ).resolved_authentik_url()
        == "https://id.example.org"
    )
    # Nothing configured -> empty (descriptor is then skipped, creds-only Secret).
    assert config.Config().resolved_authentik_url() == ""


def test_external_secret_carries_hyphenated_keys_via_index_and_includes_descriptor():
    descriptor = oidc.descriptor("https://auth.example.com", "app")
    es = externalsecrets.ExternalSecret(
        name="app-sso",
        namespace="app-ns",
        store_name="openbao",
        remote_path="app",
        template_data=descriptor,
    )
    template = es.manifest()["spec"]["target"]["template"]["data"]

    # Static descriptor values are delivered verbatim.
    assert template["issuer"] == "https://auth.example.com/application/o/app/"
    # Fetched creds are carried through with index syntax (hyphenated keys are unsafe
    # as `.client-id` in a Go template).
    assert template["client-id"] == '{{ index . "client-id" }}'
    assert template["client-secret"] == '{{ index . "client-secret" }}'


def test_descriptor_values_are_camelcased_with_clientid_and_secret():
    v = oidc.descriptor_values(
        "https://auth.example.com", "windmill-adomi", client_id="cid", secret="windmill-sso"
    )
    assert v["discoveryUrl"].endswith("/.well-known/openid-configuration")
    assert v["authorizationEndpoint"] == "https://auth.example.com/application/o/authorize/"
    assert v["clientId"] == "cid"
    assert v["secret"] == "windmill-sso"
    assert v["clientSecretKey"] == "client-secret"


def _chart_values(**over):
    base = dict(
        client_slug="adomi",
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
    base.update(over)
    return chartvalues.build_chart_values(**base)


def test_build_chart_values_injects_oidc_only_when_present():
    assert "oidc" not in _chart_values()
    values = _chart_values(oidc={"clientId": "cid", "issuer": "iss"})
    assert values["oidc"] == {"clientId": "cid", "issuer": "iss"}


def test_resolve_oidc_ready_when_no_sso():
    assert ApplicationReconciler._resolve_oidc(config.Config(), {}, "ns") == ({}, True)


def test_resolve_oidc_not_ready_until_sso_publishes_client_id(monkeypatch):
    cfg = config.Config(base_domain="example.com")
    spec = {"sso": [{"name": "windmill-adomi", "credentials": {"secret": "windmill-sso"}}]}

    def _missing(name, namespace):
        raise resolve.NotFound("nope")

    monkeypatch.setattr(resolve, "get_sso_application", _missing)
    assert ApplicationReconciler._resolve_oidc(cfg, spec, "adomi-production") == ({}, False)

    # Created but no client-id yet -> still not ready.
    monkeypatch.setattr(resolve, "get_sso_application", lambda n, ns: {"status": {}})
    assert ApplicationReconciler._resolve_oidc(cfg, spec, "adomi-production") == ({}, False)

    # client-id published -> descriptor + ready.
    monkeypatch.setattr(
        resolve,
        "get_sso_application",
        lambda n, ns: {"status": {"clientID": "cid", "slug": "windmill-adomi"}},
    )
    descriptor, ready = ApplicationReconciler._resolve_oidc(cfg, spec, "adomi-production")
    assert ready is True
    assert descriptor["clientId"] == "cid"
    assert descriptor["secret"] == "windmill-sso"
    assert descriptor["issuer"] == "https://auth.example.com/application/o/windmill-adomi/"
