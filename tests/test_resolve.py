"""Tests for Application effective-config resolution and pure helpers."""

from __future__ import annotations

import pytest

from adomi_platform_controller import resolve
from adomi_platform_controller.config import Config

_TYPE = {
    "adapter": "odoo",
    "chart": {
        "repoURL": "https://git/adomi-helm",
        "path": "charts/odoo",
        "targetRevision": "master",
    },
    "database": {"required": True},
    "sso": {"enabled": True, "protocol": "proxy"},
    "ingress": {"longpolling": True},
    "defaultValues": {"odoo": {"logLevel": "info"}},
}


def _eff(*, org=None, client=None, ws=None, app=None, type_=None, app_name="odoo", cfg=None):
    return resolve.compute(
        cfg or Config(base_domain="adomi.io"),
        org_spec=org,
        client_name="acme",
        client_spec=client or {},
        environment_name="production",
        environment_spec=ws or {"class": "production"},
        app_name=app_name,
        app_spec=app or {},
        type_spec=type_ if type_ is not None else _TYPE,
    )


def test_compute_defaults():
    eff = _eff()
    assert eff.client_slug == "acme"
    assert eff.environment_name == "production"
    assert eff.namespace == "acme-production"
    assert eff.hostname == "odoo-production-acme.adomi.io"
    assert eff.url == "https://odoo-production-acme.adomi.io"
    assert eff.chart_path == "charts/odoo"
    assert eff.type_defaults == {"odoo": {"logLevel": "info"}}


def test_compute_host_override():
    eff = _eff(app={"ingress": {"host": "odoo.acme.com"}})
    assert eff.hostname == "odoo.acme.com"


def test_helpers():
    assert resolve.namespace_name("acme", "production") == "acme-production"
    assert resolve.parse_owner_repo("git@github.com:acme/erp.git") == ("acme", "erp")
    assert resolve.sanitize_tag("feature/x") == "feature-x"
    assert resolve.built_image_ref("h", "previews", "acme", "odoo", "main") == (
        "h/previews/acme-odoo:main"
    )
    assert resolve.sanitize_default("production") is False
    assert resolve.sanitize_default("pdi") is True
    assert resolve.snapshot_object_key("ns", "snap") == "snapshots/ns/snap.pgdump"


def test_deep_merge():
    merged = resolve.deep_merge({"a": {"x": 1}}, {"a": {"y": 2}, "b": 3}, {"b": 4})
    assert merged == {"a": {"x": 1, "y": 2}, "b": 4}


def test_app_db_connection_requires_databases():
    app = {"metadata": {"name": "odoo", "namespace": "acme"}, "spec": {}, "status": {}}
    with pytest.raises(resolve.NotFound):
        resolve.app_db_connection(app)


def test_compute_domain_fqdn_overrides_base_domain():
    """A referenced Domain's fqdn overrides the org/cfg base domain for the host."""
    eff = resolve.compute(
        Config(base_domain="fallback.io"),
        org_spec={"domain": {"base": "org.example.com"}},
        client_name="acme",
        client_spec={},
        environment_name="prod",
        environment_spec={"class": "production"},
        app_name="erp",
        app_spec={},
        type_spec=_TYPE,
        domain_fqdn="acme.example.com",
    )
    assert eff.hostname == "erp-prod-acme.acme.example.com"
    assert eff.url == "https://erp-prod-acme.acme.example.com"


def test_compute_image_tag_default_and_org_override():
    cfg = Config()
    base = dict(
        org_spec=None,
        client_name="acme",
        client_spec={},
        environment_name="production",
        environment_spec={},
        app_name="erp",
        app_spec={},
        type_spec={},
    )

    eff = resolve.compute(cfg, **base)
    # The default tag must be a published one (the registry has the floating
    # 19.0 nightly, not latest — :latest broke every non-built deploy).
    assert eff.image_repository == "ghcr.io/adomi-io/odoo"
    assert eff.image_tag == "19.0"

    eff = resolve.compute(
        cfg,
        **{
            **base,
            "org_spec": {"images": {"odooRepository": "ghcr.io/acme/odoo", "odooTag": "20.0"}},
        },
    )
    assert eff.image_repository == "ghcr.io/acme/odoo"
    assert eff.image_tag == "20.0"


def test_push_image_ref_rewrites_host_for_internal_endpoint():
    public = "harbor.example.com/previews/acme-erp:master"

    # No endpoint: push exactly what will be deployed.
    assert resolve.push_image_ref(public, "") == (public, False)

    # In-cluster plain-HTTP endpoint: swap the host, flag insecure. The deploy
    # reference keeps the public host — same registry, same content.
    assert resolve.push_image_ref(public, "http://harbor-core.harbor.svc") == (
        "harbor-core.harbor.svc/previews/acme-erp:master",
        True,
    )

    # TLS endpoint (or one with a port) stays secure.
    assert resolve.push_image_ref(public, "https://registry.internal:5443/") == (
        "registry.internal:5443/previews/acme-erp:master",
        False,
    )
