"""Thin wrapper around the Kubernetes CustomObjectsApi for platform.adomi.io CRs.

Loads in-cluster config when running on the platform, otherwise a local kubeconfig.
All functions raise odoo.exceptions.UserError on failure so callers can surface a
clean message. The 'kubernetes' package is declared as an external dependency.
"""

import logging
import re

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"

# Cache the API client across calls within a worker.
_API = None


def slugify(value):
    """Reduce a string to a DNS-1123 label usable as a CR metadata.name."""
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")

    return s[:63].strip("-") or "item"


def _client():
    global _API

    if _API is not None:
        return _API

    try:
        from kubernetes import client, config
    except ImportError as exc:  # pragma: no cover
        raise UserError(
            "The 'kubernetes' Python package is not installed in this image."
        ) from exc

    try:
        config.load_incluster_config()
    except Exception:
        try:
            config.load_kube_config()
        except Exception as exc:
            raise UserError(
                "No Kubernetes configuration available (neither in-cluster nor kubeconfig)."
            ) from exc

    _API = client.CustomObjectsApi()

    return _API


def _api_exception():
    from kubernetes.client.exceptions import ApiException

    return ApiException


def get(plural, name, namespace=None):
    """Return the CR object, or None if it does not exist."""
    api = _client()
    api_exc = _api_exception()

    try:
        if namespace:
            return api.get_namespaced_custom_object(GROUP, VERSION, namespace, plural, name)

        return api.get_cluster_custom_object(GROUP, VERSION, plural, name)
    except api_exc as exc:
        if exc.status == 404:
            return None

        raise UserError("Kubernetes error reading %s/%s: %s" % (plural, name, exc.reason)) from exc


def apply(plural, name, body, namespace=None):
    """Create the CR if absent, otherwise replace it (idempotent)."""
    api = _client()
    api_exc = _api_exception()
    existing = get(plural, name, namespace)

    try:
        if existing is None:
            if namespace:
                return api.create_namespaced_custom_object(GROUP, VERSION, namespace, plural, body)

            return api.create_cluster_custom_object(GROUP, VERSION, plural, body)

        body = dict(body)
        body.setdefault("metadata", {})["resourceVersion"] = existing["metadata"]["resourceVersion"]

        if namespace:
            return api.replace_namespaced_custom_object(
                GROUP,
                VERSION,
                namespace,
                plural,
                name,
                body,
            )

        return api.replace_cluster_custom_object(GROUP, VERSION, plural, name, body)
    except api_exc as exc:
        raise UserError(
            "Kubernetes error applying %s/%s: %s" % (plural, name, getattr(exc, "reason", exc))
        ) from exc


def delete(plural, name, namespace=None):
    """Delete the CR (no-op if already gone)."""
    api = _client()
    api_exc = _api_exception()

    try:
        if namespace:
            api.delete_namespaced_custom_object(GROUP, VERSION, namespace, plural, name)
        else:
            api.delete_cluster_custom_object(GROUP, VERSION, plural, name)
    except api_exc as exc:
        if exc.status == 404:
            return

        raise UserError("Kubernetes error deleting %s/%s: %s" % (plural, name, exc.reason)) from exc


def list_(plural, namespace=None):
    """List CRs of a kind."""
    api = _client()
    api_exc = _api_exception()

    try:
        if namespace:
            res = api.list_namespaced_custom_object(GROUP, VERSION, namespace, plural)
        else:
            res = api.list_cluster_custom_object(GROUP, VERSION, plural)

        return res.get("items", [])
    except api_exc as exc:
        raise UserError("Kubernetes error listing %s: %s" % (plural, exc.reason)) from exc


def ready(obj):
    """The Ready condition status ('True'/'False'/'Unknown') or '' if absent."""
    for cond in (obj.get("status") or {}).get("conditions") or []:
        if cond.get("type") == "Ready":
            return cond.get("status") or ""

    return ""


def ready_message(obj):
    for cond in (obj.get("status") or {}).get("conditions") or []:
        if cond.get("type") == "Ready":
            return cond.get("message") or ""

    return ""
