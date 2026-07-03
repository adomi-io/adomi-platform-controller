"""Tests for the shared platform schema."""

from __future__ import annotations

import pytest

import adomi_platform_schema as schema


def test_plural_kind_catalog():
    assert schema.BY_PLURAL["clients"].kind == "Client"
    assert schema.BY_PLURAL["environments"].kind == "Environment"
    assert schema.BY_PLURAL["applications"].kind == "Application"
    assert schema.BY_PLURAL["applications"].parent == "environments"
    assert schema.BY_PLURAL["gitrepositories"].kind == "GitRepository"


def test_build_manifest():
    m = schema.build_manifest(
        "applications",
        "erp",
        {"type": "odoo"},
        client="acme",
        managed_by="adomi-platform-api",
    )
    assert m["apiVersion"] == "platform.adomi.io/v1alpha1"
    assert m["kind"] == "Application"
    assert m["metadata"]["namespace"] == "adomi-client-acme"
    assert m["metadata"]["labels"]["platform.adomi.io/client"] == "acme"
    assert m["spec"] == {"type": "odoo"}


def test_repo_path_and_namespace():
    assert schema.repo_path("environments", "dev") == "environments/dev.yaml"
    assert schema.client_namespace("acme") == "adomi-client-acme"
    assert schema.client_namespace("acme", "t-") == "t-acme"


def test_unknown_plural_rejected():
    with pytest.raises(schema.SchemaError):
        schema.resource_for_plural("organizations")  # cluster-scoped, not client-owned


@pytest.mark.parametrize("bad", ["", "Acme", "a_b", "-x", "x-", "x" * 64])
def test_validate_name_rejects(bad):
    with pytest.raises(schema.SchemaError):
        schema.validate_name(bad)
