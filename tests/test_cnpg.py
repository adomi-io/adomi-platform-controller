"""Tests for the CloudNativePG Cluster resource and naming conventions."""

from __future__ import annotations

from adomi_platform_controller.cnpg import CnpgCluster


def test_derived_names():
    assert CnpgCluster.rw_host("odoo-db") == "odoo-db-rw"
    assert CnpgCluster.app_secret_name("odoo-db") == "odoo-db-app"
    assert CnpgCluster.APP_SECRET_PASSWORD_KEY == "password"


def test_manifest_shape():
    obj = CnpgCluster(
        name="odoo-db",
        namespace="acme-erp-dev",
        instances=2,
        storage_size="20Gi",
    ).manifest()

    assert obj["apiVersion"] == "postgresql.cnpg.io/v1"
    assert obj["kind"] == "Cluster"
    assert obj["metadata"]["name"] == "odoo-db"
    assert obj["metadata"]["namespace"] == "acme-erp-dev"

    spec = obj["spec"]
    assert spec["instances"] == 2
    assert spec["storage"]["size"] == "20Gi"
    assert spec["bootstrap"]["initdb"] == {"database": "odoo", "owner": "odoo"}


def test_manifest_optional_fields_omitted():
    obj = CnpgCluster(name="odoo-db", namespace="ns").manifest()
    # No storageClass / imageName unless set.
    assert "storageClass" not in obj["spec"]["storage"]
    assert "imageName" not in obj["spec"]


def test_manifest_optional_fields_included():
    obj = CnpgCluster(
        name="odoo-db",
        namespace="ns",
        storage_class="fast",
        image_name="ghcr.io/x:16",
    ).manifest()

    assert obj["spec"]["storage"]["storageClass"] == "fast"
    assert obj["spec"]["imageName"] == "ghcr.io/x:16"
