"""Tests for the OIDC descriptor the SSOApplication publishes for downstream apps."""

from __future__ import annotations

from adomi_platform_controller import config, externalsecrets, oidc


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
