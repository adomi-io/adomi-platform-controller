"""Managed Secrets the controller writes (build credentials, db passwords, ...).

A BuildKit build runs in the ``argo`` namespace and needs a dockerconfigjson Secret
to push to Harbor and optionally a git-token Secret to clone a private repo; the
snapshot/restore jobs need object-store + db-password Secrets. :class:`ManagedSecret`
models all of these — the classmethod constructors cover the common shapes.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from .kube import TypedResource


@dataclass
class ManagedSecret(TypedResource):
    """A Secret the controller owns (``apply`` creates or updates it)."""

    GIT_TOKEN_KEY = "token"  # key used in the git-token Secret
    MANAGED_BY = "adomi-platform-controller"

    name: str
    namespace: str
    secret_type: str = "Opaque"
    string_data: dict = field(default_factory=dict)
    # When true, the Secret is created if absent and otherwise left untouched (used
    # for generate-once values like a webhook HMAC secret).
    create_only: bool = False

    @staticmethod
    def _api() -> client.CoreV1Api:
        return client.CoreV1Api()

    def _body(self) -> client.V1Secret:
        return client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=self.name,
                namespace=self.namespace,
                labels={"app.kubernetes.io/managed-by": self.MANAGED_BY},
            ),
            type=self.secret_type,
            string_data=self.string_data,
        )

    def _read(self):
        return self._api().read_namespaced_secret(self.name, self.namespace)

    def _create(self):
        return self._api().create_namespaced_secret(self.namespace, self._body())

    def _patch(self):
        if self.create_only:
            return

        self._api().patch_namespaced_secret(self.name, self.namespace, self._body())

    # --- convenience constructors ---------------------------------------------------
    @staticmethod
    def dockerconfigjson(host: str, username: str, password: str) -> dict:
        """Build a Docker config dict authenticating to one registry host (pure)."""
        auth = base64.b64encode(f"{username}:{password}".encode()).decode()

        return {
            "auths": {
                host: {
                    "username": username,
                    "password": password,
                    "auth": auth,
                },
            },
        }

    @classmethod
    def dockerconfig(cls, name, namespace, host, username, password) -> ManagedSecret:
        """A kubernetes.io/dockerconfigjson Secret for pushing to ``host``."""
        payload = json.dumps(cls.dockerconfigjson(host, username, password))

        return cls(
            name=name,
            namespace=namespace,
            secret_type="kubernetes.io/dockerconfigjson",
            string_data={".dockerconfigjson": payload},
        )

    @classmethod
    def token(cls, name, namespace, token) -> ManagedSecret:
        """An Opaque Secret holding a git token (key ``token``)."""
        return cls(
            name=name,
            namespace=namespace,
            string_data={cls.GIT_TOKEN_KEY: token},
        )

    @classmethod
    def opaque(cls, name, namespace, string_data, create_only=False) -> ManagedSecret:
        """An Opaque Secret with arbitrary data."""
        return cls(
            name=name,
            namespace=namespace,
            string_data=dict(string_data),
            create_only=create_only,
        )

    # --- reads / deletes ------------------------------------------------------------
    @staticmethod
    def read_key(name: str, namespace: str, key: str) -> str:
        """Read and base64-decode a single key from a Secret."""
        secret = client.CoreV1Api().read_namespaced_secret(name, namespace)
        raw = (secret.data or {}).get(key)

        if not raw:
            raise RuntimeError(f"secret {namespace}/{name!r} has no key {key!r}")

        return base64.b64decode(raw).decode().strip()

    @classmethod
    def delete(cls, name: str, namespace: str) -> None:
        """Delete a managed Secret by name (no-op if already gone)."""
        try:
            cls._api().delete_namespaced_secret(name, namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise


@dataclass
class ManagedConfigMap(TypedResource):
    """A ConfigMap the controller owns (``apply`` creates or updates it).

    Used for non-secret payloads a Job needs as a file — e.g. the provisioning SQL,
    mounted and run with ``psql -f`` so no shell ever interpolates the SQL content.
    """

    MANAGED_BY = "adomi-platform-controller"

    name: str
    namespace: str
    data: dict = field(default_factory=dict)

    @staticmethod
    def _api() -> client.CoreV1Api:
        return client.CoreV1Api()

    def _body(self) -> client.V1ConfigMap:
        return client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=self.name,
                namespace=self.namespace,
                labels={"app.kubernetes.io/managed-by": self.MANAGED_BY},
            ),
            data=dict(self.data),
        )

    def _read(self):
        return self._api().read_namespaced_config_map(self.name, self.namespace)

    def _create(self):
        return self._api().create_namespaced_config_map(self.namespace, self._body())

    def _patch(self):
        self._api().patch_namespaced_config_map(self.name, self.namespace, self._body())

    @classmethod
    def delete(cls, name: str, namespace: str) -> None:
        """Delete a managed ConfigMap by name (no-op if already gone)."""
        try:
            cls._api().delete_namespaced_config_map(name, namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
