"""SSOApplication resources (the controller's own identity.adomi.io CRD).

The Application engine does not talk to Authentik directly: it declares an
SSOApplication and lets the SSOApplicationReconciler provision the Authentik
provider/application and OpenBao credentials. Two flavours:

- :class:`ProxySSOApplication` — a forward-auth provider for apps with no native SSO
  (Odoo, Mailpit); the app's Ingress is gated by the Authentik outpost.
- :class:`OAuth2SSOApplication` — an OIDC provider for apps with native SSO
  (Superset); the client credentials are published into a target Secret.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .kube import CustomResource


class SSOApplication(CustomResource):
    """Shared base: the identity.adomi.io/SSOApplication CRD coordinates + metadata.

    (``auth_group`` is the Authentik dashboard category; it is named distinctly from
    the CRD API ``group`` this base inherits from :class:`CustomResource`.)
    """

    group = "identity.adomi.io"
    version = "v1alpha1"
    plural = "ssoapplications"

    def _metadata(self) -> dict:
        metadata: dict = {"name": self.name, "namespace": self.namespace}

        if self.labels:
            metadata["labels"] = self.labels

        if self.owner_references:
            metadata["ownerReferences"] = self.owner_references

        return metadata


@dataclass
class ProxySSOApplication(SSOApplication):
    """A forward-auth SSOApplication gating an app's Ingress."""

    name: str
    namespace: str
    display_name: str
    external_host: str  # the public URL the provider protects (https://<host>)
    slug: str = ""  # globally-unique Authentik slug (defaults to name in-backend)
    mode: str = "forwardSingle"  # SSOApplication proxy.mode
    auth_group: str = ""  # Authentik dashboard category
    labels: dict[str, str] | None = None
    owner_references: list[dict] | None = None

    def manifest(self) -> dict:
        spec: dict = {
            "protocol": "proxy",
            "displayName": self.display_name,
            "proxy": {"mode": self.mode, "externalHost": self.external_host},
        }

        if self.slug:
            spec["slug"] = self.slug

        if self.auth_group:
            spec["group"] = self.auth_group

        return {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "SSOApplication",
            "metadata": self._metadata(),
            "spec": spec,
        }


@dataclass
class OAuth2SSOApplication(SSOApplication):
    """An OAuth2/OIDC SSOApplication that publishes credentials to a Secret."""

    name: str
    namespace: str
    display_name: str
    redirect_uris: list[str]
    target_secret: str  # Secret the client-id/client-secret are published into
    slug: str = ""
    auth_group: str = ""  # Authentik dashboard category
    scopes: list[str] = field(default_factory=list)
    labels: dict[str, str] | None = None
    owner_references: list[dict] | None = None

    def manifest(self) -> dict:
        spec: dict = {
            "protocol": "oauth2",
            "displayName": self.display_name,
            "redirectUris": list(self.redirect_uris),
            "credentials": {"targetSecret": {"name": self.target_secret}},
        }

        if self.slug:
            spec["slug"] = self.slug

        if self.auth_group:
            spec["group"] = self.auth_group

        if self.scopes:
            spec["scopes"] = list(self.scopes)

        return {
            "apiVersion": f"{self.group}/{self.version}",
            "kind": "SSOApplication",
            "metadata": self._metadata(),
            "spec": spec,
        }
