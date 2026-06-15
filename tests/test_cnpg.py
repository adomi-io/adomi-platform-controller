"""Tests for the CloudNativePG Cluster builder and naming conventions."""

from __future__ import annotations

from adomi_platform_controller import cnpg


def test_derived_names():
    assert cnpg.rw_host("odoo-db") == "odoo-db-rw"
    assert cnpg.app_secret_name("odoo-db") == "odoo-db-app"
    assert cnpg.APP_SECRET_PASSWORD_KEY == "password"


def test_build_shape():
    obj = cnpg.build(
        cnpg.Spec(name="odoo-db", namespace="acme-erp-dev", instances=2, storage_size="20Gi")
    )

    assert obj["apiVersion"] == "postgresql.cnpg.io/v1"
    assert obj["kind"] == "Cluster"
    assert obj["metadata"]["name"] == "odoo-db"
    assert obj["metadata"]["namespace"] == "acme-erp-dev"

    spec = obj["spec"]
    assert spec["instances"] == 2
    assert spec["storage"]["size"] == "20Gi"
    assert spec["bootstrap"]["initdb"] == {"database": "odoo", "owner": "odoo"}


def test_build_optional_fields_omitted():
    obj = cnpg.build(cnpg.Spec(name="odoo-db", namespace="ns"))
    # No storageClass / imageName unless set.
    assert "storageClass" not in obj["spec"]["storage"]
    assert "imageName" not in obj["spec"]


def test_build_optional_fields_included():
    obj = cnpg.build(
        cnpg.Spec(name="odoo-db", namespace="ns", storage_class="fast", image_name="ghcr.io/x:16")
    )
    assert obj["spec"]["storage"]["storageClass"] == "fast"
    assert obj["spec"]["imageName"] == "ghcr.io/x:16"
