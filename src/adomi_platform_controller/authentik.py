"""Authentik API access through the official authentik-client.

This wraps the generated client with the create-or-update-by-name/slug semantics
an SSOApplication needs across OAuth2 providers, applications, and the scope
mappings they reference. OpenBao is the source of truth for credentials; this
client makes Authentik match it.

The authentik-client package is generated from Authentik's OpenAPI spec and is
versioned to match an Authentik release, so keep the dependency in step with the
Authentik you run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import authentik_client
from authentik_client.exceptions import NotFoundException


@dataclass
class OAuth2ProviderSpec:
    """The desired state of an OAuth2 provider."""

    name: str
    authorization_flow_pk: str
    invalidation_flow_pk: str
    client_id: str
    client_secret: str
    redirect_uris: list[str]
    property_mapping_pks: list[str] = field(default_factory=list)
    signing_key_pk: str = ""  # optional


class AuthentikClient:
    """Talks to a single Authentik server with a bearer API token."""

    def __init__(self, addr: str, token: str) -> None:
        config = authentik_client.Configuration(host=f"{addr.rstrip('/')}/api/v3")
        config.access_token = token
        api = authentik_client.ApiClient(config)
        self._core = authentik_client.CoreApi(api)
        self._flows = authentik_client.FlowsApi(api)
        self._crypto = authentik_client.CryptoApi(api)
        self._providers = authentik_client.ProvidersApi(api)
        self._mappings = authentik_client.PropertymappingsApi(api)

    @staticmethod
    def _first_pk(results: list | None) -> str:
        """Return the string pk of the first result, or '' when there are none."""
        if not results:
            return ""
        return str(results[0].pk or "")

    def verify(self) -> None:
        """Check that the API token is accepted."""
        self._core.core_users_me_retrieve()

    def flow_pk(self, slug: str) -> str:
        """Resolve a flow slug to its pk. Returns '' when not found."""
        page = self._flows.flows_instances_list(slug=slug)
        return self._first_pk(page.results)

    def signing_key_pk(self, name: str) -> str:
        """Resolve a certificate-keypair by name to its pk. Returns '' when absent."""
        page = self._crypto.crypto_certificatekeypairs_list(name=name)
        return self._first_pk(page.results)

    def ensure_scope_mapping(self, scope_name: str) -> str | None:
        """Resolve a scope mapping to its pk, creating the "groups" mapping if missing.

        Authentik ships openid/profile/email but not groups. Returns None when a
        non-groups scope cannot be resolved.
        """
        page = self._mappings.propertymappings_provider_scope_list(scope_name=scope_name)
        pk = self._first_pk(page.results)
        if pk:
            return pk

        if scope_name != "groups":
            return None

        created = self._mappings.propertymappings_provider_scope_create(
            scope_mapping_request=authentik_client.ScopeMappingRequest(
                name="Groups (adomi-platform-controller)",
                scope_name="groups",
                expression='return {"groups": [group.name for group in user.ak_groups.all()]}',
            )
        )
        return str(created.pk) or None

    def ensure_oauth2_provider(self, spec: OAuth2ProviderSpec) -> int:
        """Create or update the provider matched by name; return its pk.

        Credentials come from the spec verbatim (OpenBao is the source of truth),
        so an existing provider is patched to match.
        """
        fields = {
            "name": spec.name,
            "authorization_flow": spec.authorization_flow_pk,
            "invalidation_flow": spec.invalidation_flow_pk,
            "client_type": "confidential",
            "client_id": spec.client_id,
            "client_secret": spec.client_secret,
            "property_mappings": spec.property_mapping_pks,
            "redirect_uris": [
                authentik_client.RedirectURIRequest(matching_mode="strict", url=u)
                for u in spec.redirect_uris
            ],
        }
        if spec.signing_key_pk:
            fields["signing_key"] = spec.signing_key_pk

        page = self._providers.providers_oauth2_list(search=spec.name)
        for provider in page.results:
            if provider.name == spec.name:
                self._providers.providers_oauth2_partial_update(
                    id=provider.pk,
                    patched_o_auth2_provider_request=authentik_client.PatchedOAuth2ProviderRequest(
                        **fields
                    ),
                )
                return provider.pk

        created = self._providers.providers_oauth2_create(
            o_auth2_provider_request=authentik_client.OAuth2ProviderRequest(**fields)
        )
        return created.pk

    def ensure_application(self, slug: str, name: str, provider_pk: int) -> str:
        """Create or update the application (looked up by slug); return its pk (a UUID)."""
        try:
            existing = self._core.core_applications_retrieve(slug=slug)
        except NotFoundException:
            created = self._core.core_applications_create(
                application_request=authentik_client.ApplicationRequest(
                    name=name, slug=slug, provider=provider_pk
                )
            )
            return str(created.pk)

        self._core.core_applications_partial_update(
            slug=slug,
            patched_application_request=authentik_client.PatchedApplicationRequest(
                name=name, provider=provider_pk
            ),
        )
        return str(existing.pk)

    def delete_application(self, slug: str) -> None:
        """Remove the application by slug. A missing application is not an error."""
        try:
            self._core.core_applications_destroy(slug=slug)
        except NotFoundException:
            return

    def delete_provider(self, pk: int) -> None:
        """Remove the OAuth2 provider by pk. A missing provider is not an error."""
        try:
            self._providers.providers_oauth2_destroy(id=pk)
        except NotFoundException:
            return
