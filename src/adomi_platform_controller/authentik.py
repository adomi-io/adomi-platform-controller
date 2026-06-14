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


@dataclass
class ProxyProviderSpec:
    """The desired state of a proxy provider (forward-auth / reverse-proxy).

    Authentik generates and owns the proxy client credentials internally, so -
    unlike OAuth2 - none are supplied here.
    """

    name: str
    authorization_flow_pk: str
    invalidation_flow_pk: str
    external_host: str
    mode: str  # "forward_single" | "forward_domain" | "proxy"
    authentication_flow_pk: str = ""  # optional; login flow for un-authed users
    cookie_domain: str = ""  # forward_domain only
    internal_host: str = ""  # proxy mode only
    skip_path_regex: str = ""  # optional; paths that bypass auth
    property_mapping_pks: list[str] = field(default_factory=list)


@dataclass
class ApplicationSpec:
    """The desired state of an Authentik application's metadata.

    All metadata fields are optional; empty values are left unset so Authentik keeps
    its default. icon accepts an image URL or an "fa://fa-name" FontAwesome reference.
    """

    slug: str
    name: str
    provider_pk: int | None = None
    group: str = ""  # dashboard category apps are grouped under
    icon: str = ""  # image URL or "fa://fa-name"
    description: str = ""
    publisher: str = ""
    launch_url: str = ""
    backchannel_provider_pks: list[int] = field(default_factory=list)


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
        self._outposts = authentik_client.OutpostsApi(api)

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

    def ensure_group(self, name: str) -> str:
        """Ensure a group exists by name; return its pk.

        Membership is managed in Authentik, not here - this only guarantees the
        group is present so apps can reference it by name in their SSO RBAC rules.
        """
        page = self._core.core_groups_list(name=name, include_users=False)
        for group in page.results:
            if group.name == name:
                return str(group.pk)
        created = self._core.core_groups_create(
            group_request=authentik_client.GroupRequest(name=name)
        )
        return str(created.pk)

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
            # Authentik (2024.12+) gates each provider on an explicit grant-type
            # allowlist; an empty list permits no grants and the OIDC code flow
            # fails with "Invalid grant_type for provider". Enable the standard
            # authorization-code flow plus refresh tokens for web SSO clients.
            "grant_types": [
                authentik_client.GrantTypesEnum.AUTHORIZATION_CODE,
                authentik_client.GrantTypesEnum.REFRESH_TOKEN,
            ],
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

    def ensure_proxy_provider(self, spec: ProxyProviderSpec) -> int:
        """Create or update the proxy provider matched by name; return its pk."""
        fields = {
            "name": spec.name,
            "authorization_flow": spec.authorization_flow_pk,
            "invalidation_flow": spec.invalidation_flow_pk,
            "external_host": spec.external_host,
            "mode": authentik_client.ProxyMode(spec.mode),
            "property_mappings": spec.property_mapping_pks,
        }
        if spec.authentication_flow_pk:
            fields["authentication_flow"] = spec.authentication_flow_pk
        if spec.cookie_domain:
            fields["cookie_domain"] = spec.cookie_domain
        if spec.internal_host:
            fields["internal_host"] = spec.internal_host
        if spec.skip_path_regex:
            fields["skip_path_regex"] = spec.skip_path_regex

        page = self._providers.providers_proxy_list(search=spec.name)
        for provider in page.results:
            if provider.name == spec.name:
                self._providers.providers_proxy_partial_update(
                    id=provider.pk,
                    patched_proxy_provider_request=authentik_client.PatchedProxyProviderRequest(
                        **fields
                    ),
                )
                return provider.pk

        created = self._providers.providers_proxy_create(
            proxy_provider_request=authentik_client.ProxyProviderRequest(**fields)
        )
        return created.pk

    def _find_outpost(self, name: str):
        """Return the outpost matched by exact name, or None."""
        page = self._outposts.outposts_instances_list(search=name)
        for outpost in page.results:
            if outpost.name == name:
                return outpost
        return None

    def ensure_outpost_provider(
        self, outpost_name: str, provider_pk: int, browser_host: str = ""
    ) -> None:
        """Attach a provider to an outpost (merge) and, when browser_host is set, point
        the outpost's browser-facing URL at it.

        Used for the built-in embedded outpost the Authentik server serves at
        /outpost.goauthentik.io/. browser_host must be the public Authentik URL: forward
        auth reaches the outpost over the internal Service URL, so the outpost can't infer
        the external host and would otherwise redirect the browser to localhost. Setting
        authentik_host_browser fixes the authorize-endpoint redirect.
        """
        outpost = self._find_outpost(outpost_name)
        if outpost is None:
            raise RuntimeError(f"outpost {outpost_name!r} not found in Authentik")

        providers = sorted(set(outpost.providers or []) | {provider_pk})
        config = dict(outpost.config or {})
        config_changed = bool(browser_host) and config.get("authentik_host_browser") != browser_host
        if browser_host:
            config["authentik_host_browser"] = browser_host

        if providers == sorted(outpost.providers or []) and not config_changed:
            return

        fields: dict = {"providers": providers}
        if config_changed:
            fields["config"] = config
        self._outposts.outposts_instances_partial_update(
            uuid=outpost.pk,
            patched_outpost_request=authentik_client.PatchedOutpostRequest(**fields),
        )

    def remove_outpost_provider(self, outpost_name: str, provider_pk: int) -> None:
        """Remove a provider from an outpost's provider list. Missing is not an error."""
        outpost = self._find_outpost(outpost_name)
        if outpost is None or provider_pk not in (outpost.providers or []):
            return
        providers = [p for p in outpost.providers if p != provider_pk]
        self._outposts.outposts_instances_partial_update(
            uuid=outpost.pk,
            patched_outpost_request=authentik_client.PatchedOutpostRequest(providers=providers),
        )

    def find_provider_pk(self, name: str) -> int | None:
        """Resolve a provider (OAuth2 or proxy) by exact name to its pk, or None.

        Used to turn backchannel-provider names into the pks Authentik expects.
        """
        for lister in (self._providers.providers_oauth2_list, self._providers.providers_proxy_list):
            for provider in lister(search=name).results:
                if provider.name == name:
                    return provider.pk
        return None

    def ensure_application(self, spec: ApplicationSpec) -> str:
        """Create or update the application (looked up by slug); return its pk (a UUID).

        Metadata fields are only sent when set, so empty values keep Authentik's
        defaults rather than clearing existing data.
        """
        fields: dict = {"name": spec.name}
        if spec.provider_pk is not None:
            fields["provider"] = spec.provider_pk
        if spec.group:
            fields["group"] = spec.group
        if spec.icon:
            fields["meta_icon"] = spec.icon
        if spec.description:
            fields["meta_description"] = spec.description
        if spec.publisher:
            fields["meta_publisher"] = spec.publisher
        if spec.launch_url:
            fields["meta_launch_url"] = spec.launch_url
        if spec.backchannel_provider_pks:
            fields["backchannel_providers"] = spec.backchannel_provider_pks

        try:
            existing = self._core.core_applications_retrieve(slug=spec.slug)
        except NotFoundException:
            created = self._core.core_applications_create(
                application_request=authentik_client.ApplicationRequest(slug=spec.slug, **fields)
            )
            return str(created.pk)

        self._core.core_applications_partial_update(
            slug=spec.slug,
            patched_application_request=authentik_client.PatchedApplicationRequest(**fields),
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

    def delete_proxy_provider(self, pk: int) -> None:
        """Remove the proxy provider by pk. A missing provider is not an error."""
        try:
            self._providers.providers_proxy_destroy(id=pk)
        except NotFoundException:
            return
