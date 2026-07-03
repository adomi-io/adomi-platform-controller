"""The per-environment Namespace the controller owns.

Each Environment gets its own namespace so an environment's applications, their
databases and its SSO/ExternalSecret resources can be torn down together by deleting
one namespace (important for ephemeral preview environments).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes import client

from .kube import TypedResource


@dataclass
class Namespace(TypedResource):
    """A cluster-scoped Namespace (``apply`` ensures it; ``delete`` removes it)."""

    # A namespace that is already terminating returns 409; tolerate it on delete.
    DELETE_IGNORE_STATUSES = (404, 409)

    name: str
    labels: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def _api() -> client.CoreV1Api:
        return client.CoreV1Api()

    def _body(self) -> client.V1Namespace:
        return client.V1Namespace(
            metadata=client.V1ObjectMeta(name=self.name, labels=self.labels or {}),
        )

    def _read(self):
        return self._api().read_namespace(self.name)

    def _create(self):
        return self._api().create_namespace(self._body())

    def _patch(self):
        if self.labels:
            self._api().patch_namespace(self.name, {"metadata": {"labels": self.labels}})

    def _delete(self):
        return self._api().delete_namespace(self.name)
