"""Builds and applies SSOApplication objects.

The Application engine does not talk to Authentik directly: it declares an
SSOApplication (the controller's own identity.adomi.io CRD) and lets the existing
SSOApplicationReconciler provision the Authentik provider/application and OpenBao
credentials. This keeps the SSO logic in one place.

Two flavours: a *proxy* (forward-auth) provider for apps with no native SSO
(Odoo, Mailpit) — the app's Ingress is gated by the Authentik outpost; and an
*oauth2* provider for apps with native OIDC (Superset) — the client credentials are
published into a target Secret the app consumes. We write the object with the
dynamic CustomObjectsApi.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

GROUP = "identity.adomi.io"
VERSION = "v1alpha1"
PLURAL = "ssoapplications"


@dataclass
class ProxySpec:
    """Describes a forward-auth SSOApplication for an app's Ingress."""

    name: str
    namespace: str
    display_name: str
    external_host: str  # the public URL the provider protects (https://<host>)
    slug: str = ""  # globally-unique Authentik slug (defaults to name in-backend)
    mode: str = "forwardSingle"  # SSOApplication proxy.mode
    group: str = ""  # Authentik dashboard category
    labels: dict[str, str] | None = None
    owner_references: list[dict] | None = None


@dataclass
class OAuth2Spec:
    """Describes an OAuth2/OIDC SSOApplication that publishes credentials to a Secret."""

    name: str
    namespace: str
    display_name: str
    redirect_uris: list[str]
    target_secret: str  # Secret the client-id/client-secret are published into
    slug: str = ""
    group: str = ""
    scopes: list[str] = field(default_factory=list)
    labels: dict[str, str] | None = None
    owner_references: list[dict] | None = None


def _metadata(s) -> dict:
    metadata: dict = {"name": s.name, "namespace": s.namespace}
    if s.labels:
        metadata["labels"] = s.labels
    if s.owner_references:
        metadata["ownerReferences"] = s.owner_references
    return metadata


def build(s: ProxySpec) -> dict:
    """Build a proxy-protocol SSOApplication object for the spec."""
    spec: dict = {
        "protocol": "proxy",
        "displayName": s.display_name,
        "proxy": {"mode": s.mode, "externalHost": s.external_host},
    }
    if s.slug:
        spec["slug"] = s.slug
    if s.group:
        spec["group"] = s.group
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "SSOApplication",
        "metadata": _metadata(s),
        "spec": spec,
    }


def build_oauth2(s: OAuth2Spec) -> dict:
    """Build an oauth2-protocol SSOApplication object for the spec."""
    spec: dict = {
        "protocol": "oauth2",
        "displayName": s.display_name,
        "redirectUris": list(s.redirect_uris),
        "credentials": {"targetSecret": {"name": s.target_secret}},
    }
    if s.slug:
        spec["slug"] = s.slug
    if s.group:
        spec["group"] = s.group
    if s.scopes:
        spec["scopes"] = list(s.scopes)
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "SSOApplication",
        "metadata": _metadata(s),
        "spec": spec,
    }


def _apply(namespace: str, name: str, desired: dict) -> None:
    api = client.CustomObjectsApi()
    try:
        api.get_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name)
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, desired)
        return
    api.patch_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name, desired)


def apply(s: ProxySpec) -> None:
    """Create or update a proxy SSOApplication (idempotent)."""
    _apply(s.namespace, s.name, build(s))


def apply_oauth2(s: OAuth2Spec) -> None:
    """Create or update an oauth2 SSOApplication (idempotent)."""
    _apply(s.namespace, s.name, build_oauth2(s))


def delete(name: str, namespace: str) -> None:
    """Delete the SSOApplication (no-op if already gone).

    The SSOApplication's own finalizer best-effort cleans up the Authentik
    application/provider before the object is removed.
    """
    api = client.CustomObjectsApi()
    try:
        api.delete_namespaced_custom_object(GROUP, VERSION, namespace, PLURAL, name)
    except ApiException as exc:
        if exc.status != 404:
            raise
