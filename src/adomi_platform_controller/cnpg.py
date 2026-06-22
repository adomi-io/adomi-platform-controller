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
    APP_SECRET_SUFFIX = "-app"  # Secret with the application role credentials
    APP_SECRET_PASSWORD_KEY = "password"  # key within the -app secret

    name: str
    namespace: str
    instances: int = 1
    storage_size: str = "10Gi"
    storage_class: str = ""  # empty = cluster default StorageClass
    database: str = "odoo"  # bootstrapped application database
    owner: str = "odoo"  # bootstrapped application role
    image_name: str = ""  # empty = CNPG operator default
    labels: dict[str, str] = field(default_factory=dict)
    owner_references: list[dict] = field(default_factory=list)

    @classmethod
    def rw_host(cls, cluster_name: str) -> str:
        """The read-write Service host CNPG creates for the cluster's primary."""
        return f"{cluster_name}{cls.RW_SERVICE_SUFFIX}"

    @classmethod
    def app_secret_name(cls, cluster_name: str) -> str:
        """The Secret CNPG generates holding the application role's credentials."""
        return f"{cluster_name}{cls.APP_SECRET_SUFFIX}"

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

        if self.image_name:
            spec["imageName"] = self.image_name

        return {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "Cluster",
            "metadata": metadata,
            "spec": spec,
        }
