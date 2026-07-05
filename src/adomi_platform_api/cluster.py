"""Read live resource status from the cluster.

Writes go to git (desired state); reads come from the custom resources Argo CD has
applied, whose ``.status`` the controller maintains. The kubernetes client is imported
lazily so the package imports without it (e.g. in unit tests that stub the reader).
"""

from __future__ import annotations

from adomi_platform_schema import GROUP, VERSION


class ClusterError(Exception):
    """A cluster read failed (no config, or an API error)."""


class ClusterReader:
    """Reads ``platform.adomi.io`` custom resources from the cluster."""

    def __init__(self):
        self._api = None

    def _client(self):
        if self._api is not None:
            return self._api

        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except Exception:
            try:
                config.load_kube_config()
            except Exception as exc:
                raise ClusterError("no Kubernetes configuration available") from exc

        self._api = client.CustomObjectsApi()
        return self._api

    def get(self, plural: str, namespace: str, name: str, *, group: str = GROUP) -> dict | None:
        from kubernetes.client.exceptions import ApiException

        try:
            return self._client().get_namespaced_custom_object(
                group, VERSION, namespace, plural, name
            )
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise ClusterError(f"reading {plural}/{name}: {exc.reason}") from exc

    def list(
        self,
        plural: str,
        namespace: str | None = None,
        label_selector: str = "",
        *,
        group: str = GROUP,
    ) -> list[dict]:
        from kubernetes.client.exceptions import ApiException

        try:
            if namespace:
                res = self._client().list_namespaced_custom_object(
                    group, VERSION, namespace, plural, label_selector=label_selector
                )
            else:
                res = self._client().list_cluster_custom_object(
                    group, VERSION, plural, label_selector=label_selector
                )
        except ApiException as exc:
            raise ClusterError(f"listing {plural}: {exc.reason}") from exc

        return res.get("items", [])
