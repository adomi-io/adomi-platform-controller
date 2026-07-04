"""CloudNativePG Cluster resource.

For in-cluster environments (dev / PDI / preview) the platform provisions a
CloudNativePG Cluster per environment or as a standalone managed Database. CNPG
creates, by convention, a ``<cluster>-rw`` Service pointing at the primary and a
``<cluster>-app`` Secret holding the application role's credentials (keys
``username``/``password``/...). Consumers wire to the ``-rw`` host and the
``password`` key of the ``-app`` secret.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .kube import CustomResource


@dataclass
class CnpgCluster(CustomResource):
    """A CloudNativePG Cluster for one environment / managed database."""

    group = "postgresql.cnpg.io"
    version = "v1"
    plural = "clusters"

    # CNPG's conventional generated names, derived from the Cluster name.
    RW_SERVICE_SUFFIX = "-rw"  # read-write Service (the primary)
    SUPERUSER_SECRET_SUFFIX = "-superuser"  # Secret with the superuser credentials
    SUPERUSER = "postgres"  # the superuser role enableSuperuserAccess exposes

    name: str
    namespace: str
    database: str  # bootstrapped application database
    owner: str  # bootstrapped application role
    instances: int = 1
    storage_size: str = "10Gi"
    storage_class: str = ""  # empty = cluster default StorageClass
    image_name: str = ""  # empty = CNPG operator default
    # Expose the superuser via a generated -superuser Secret. Needed when something
    # other than the owner role (e.g. the database provisioner) must create databases
    # and roles on the cluster.
    enable_superuser_access: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    owner_references: list[dict] = field(default_factory=list)

    @classmethod
    def rw_host(cls, cluster_name: str) -> str:
        """The read-write Service host CNPG creates for the cluster's primary."""
        return f"{cluster_name}{cls.RW_SERVICE_SUFFIX}"

    @classmethod
    def superuser_secret_name(cls, cluster_name: str) -> str:
        """The Secret CNPG generates for the superuser when superuser access is on."""
        return f"{cluster_name}{cls.SUPERUSER_SECRET_SUFFIX}"

    def manifest(self) -> dict:
        metadata: dict = {"name": self.name, "namespace": self.namespace}

        if self.labels:
            metadata["labels"] = self.labels

        if self.owner_references:
            metadata["ownerReferences"] = self.owner_references

        storage: dict = {"size": self.storage_size}

        if self.storage_class:
            storage["storageClass"] = self.storage_class

        spec: dict = {
            "instances": self.instances,
            "storage": storage,
            "bootstrap": {"initdb": {"database": self.database, "owner": self.owner}},
        }

        if self.enable_superuser_access:
            spec["enableSuperuserAccess"] = True

        if self.image_name:
            spec["imageName"] = self.image_name

        return {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "Cluster",
            "metadata": metadata,
            "spec": spec,
        }
