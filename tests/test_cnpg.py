"""Tests for the CloudNativePG Cluster resource and naming conventions."""

from __future__ import annotations

from adomi_platform_controller.cnpg import CnpgCluster


def _cluster(**overrides):
    base = {"name": "app-db", "namespace": "ns", "database": "app", "owner": "app"}
    base.update(overrides)
    return CnpgCluster(**base)


def test_derived_names():
    assert CnpgCluster.rw_host("app-db") == "app-db-rw"
    assert CnpgCluster.superuser_secret_name("app-db") == "app-db-superuser"


def test_superuser_access_flag():
    # Off by default; opt-in adds enableSuperuserAccess so a provisioner can connect.
    assert "enableSuperuserAccess" not in _cluster().manifest()["spec"]

    obj = _cluster(enable_superuser_access=True).manifest()
    assert obj["spec"]["enableSuperuserAccess"] is True


def test_manifest_shape():
    obj = _cluster(
        name="erp-db",
        namespace="acme-erp-dev",
        database="erp",
        owner="erp",
        instances=2,
        storage_size="20Gi",
    ).manifest()

    assert obj["apiVersion"] == "postgresql.cnpg.io/v1"
    assert obj["kind"] == "Cluster"
    assert obj["metadata"]["name"] == "erp-db"
    assert obj["metadata"]["namespace"] == "acme-erp-dev"

    spec = obj["spec"]
    assert spec["instances"] == 2
    assert spec["storage"]["size"] == "20Gi"
    assert spec["bootstrap"]["initdb"] == {"database": "erp", "owner": "erp"}


def test_manifest_optional_fields_omitted():
    obj = _cluster().manifest()
    # No storageClass / imageName unless set.
    assert "storageClass" not in obj["spec"]["storage"]
    assert "imageName" not in obj["spec"]


def test_manifest_optional_fields_included():
    obj = _cluster(
        storage_class="fast",
        image_name="ghcr.io/x:16",
    ).manifest()

    assert obj["spec"]["storage"]["storageClass"] == "fast"
    assert obj["spec"]["imageName"] == "ghcr.io/x:16"
