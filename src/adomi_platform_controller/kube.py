"""Base classes for the Kubernetes resources the controller manages.

``CustomResource`` wraps the dynamic ``CustomObjectsApi`` (CRDs: CNPG Clusters, Argo
CD Applications, Argo Workflows, ExternalSecrets, SSOApplications). It centralises the
create-or-update / delete-ignore-404 boilerplate that every resource module used to
repeat; a subclass only declares ``group``/``version``/``plural`` and builds its
``manifest()``. The default ``apply()`` is create-or-merge-patch; subclasses override
it where the semantics differ (replace, create-only, ...).
"""

from __future__ import annotations

from kubernetes import client
from kubernetes.client.exceptions import ApiException


class CustomResource:
    """A namespaced custom resource managed via the dynamic CustomObjectsApi.

    Subclasses set the ``group``/``version``/``plural`` class attributes, expose
    ``name`` and ``namespace`` instance attributes (typically as dataclass fields),
    and implement ``manifest()``.
    """

    group: str = ""
    version: str = ""
    plural: str = ""

    # Subclasses provide `name` and `namespace` (dataclass fields).

    def manifest(self) -> dict:
        """Build the full custom-resource object. Implemented by subclasses."""
        raise NotImplementedError

    @classmethod
    def _api(cls) -> client.CustomObjectsApi:
        return client.CustomObjectsApi()

    @classmethod
    def read(cls, name: str, namespace: str) -> dict | None:
        """Fetch the resource by name, or None if it does not exist."""
        try:
            return cls._api().get_namespaced_custom_object(
                cls.group,
                cls.version,
                namespace,
                cls.plural,
                name,
            )
        except ApiException as exc:
            if exc.status == 404:
                return None

            raise

    def get(self) -> dict | None:
        """Fetch this resource, or None if it does not exist."""
        return self.read(self.name, self.namespace)

    def create(self) -> dict:
        return self._api().create_namespaced_custom_object(
            self.group,
            self.version,
            self.namespace,
            self.plural,
            self.manifest(),
        )

    def patch(self) -> dict:
        return self._api().patch_namespaced_custom_object(
            self.group,
            self.version,
            self.namespace,
            self.plural,
            self.name,
            self.manifest(),
        )

    def replace(self, body: dict) -> dict:
        return self._api().replace_namespaced_custom_object(
            self.group,
            self.version,
            self.namespace,
            self.plural,
            self.name,
            body,
        )

    def apply(self) -> dict:
        """Create the resource if absent, otherwise merge-patch it (idempotent)."""
        if self.get() is None:
            return self.create()

        return self.patch()

    @classmethod
    def delete(cls, name: str, namespace: str) -> None:
        """Delete the resource by name (no-op if already gone)."""
        try:
            cls._api().delete_namespaced_custom_object(
                cls.group,
                cls.version,
                namespace,
                cls.plural,
                name,
            )
        except ApiException as exc:
            if exc.status != 404:
                raise


class TypedResource:
    """A resource managed via a typed Kubernetes API (Secrets, Namespaces, Ingresses).

    Centralises the same create-or-update / delete-ignore-404 flow as
    :class:`CustomResource`, but over the typed clients (whose method names differ per
    kind). Subclasses implement the four thin hooks ``_read``/``_create``/``_patch``/
    ``_delete`` (each a single typed-API call); the base orchestrates them.
    """

    # The 404 statuses ``delete`` tolerates (subclasses may widen, e.g. 409 for a
    # namespace that is already terminating).
    DELETE_IGNORE_STATUSES = (404,)

    def _read(self):
        raise NotImplementedError

    def _create(self):
        raise NotImplementedError

    def _patch(self):
        raise NotImplementedError

    def _delete(self):
        raise NotImplementedError

    def apply(self) -> None:
        """Create the resource if absent, otherwise update it (idempotent)."""
        try:
            self._read()
        except ApiException as exc:
            if exc.status != 404:
                raise

            self._create()
            return

        self._patch()

    def delete(self) -> None:
        """Delete the resource (no-op if already gone)."""
        try:
            self._delete()
        except ApiException as exc:
            if exc.status not in self.DELETE_IGNORE_STATUSES:
                raise
