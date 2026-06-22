"""SSOApplicationReconciler.

An SSOApplication is the single, self-contained platform resource: creating one
provisions everything needed for an app to use single sign-on. The reconciler
generates OAuth credentials in OpenBao (once, never overwritten), resolves the
shared Authentik references (flows, signing key, scope mappings), creates or
updates the Authentik OAuth2 provider and application, and publishes the
credentials into the app namespace via an ExternalSecret.

On deletion a finalizer best-effort deletes the Authentik application and
provider; OAuth credentials in OpenBao are intentionally retained so a recreated
application reuses the same client id/secret.
"""

from __future__ import annotations

import kopf

from .. import conditions, externalsecrets, secretgen, state
from ..authentik import ApplicationSpec, OAuth2ProviderSpec, ProxyProviderSpec
from ._common import fail

GROUP = "identity.adomi.io"
VERSION = "v1alpha1"
PLURAL = "ssoapplications"

# Requested when an SSOApplication does not declare any scopes.
DEFAULT_SCOPES = ["openid", "profile", "email", "groups"]

PROTOCOL_OAUTH2 = "oauth2"
PROTOCOL_PROXY = "proxy"

# The built-in outpost the Authentik server serves at /outpost.goauthentik.io/;
# proxy providers attach here unless the SSOApplication names another outpost.
DEFAULT_OUTPOST = "authentik Embedded Outpost"

# SSOApplication proxy.mode -> Authentik ProxyMode value.
PROXY_MODES = {
    "forwardSingle": "forward_single",
    "forwardDomain": "forward_domain",
    "proxy": "proxy",
}


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def reconcile(spec, meta, status, patch, name, namespace, logger, **_) -> None:
    generation = meta.get("generation", 0)
    provider = state.provider()
    cfg = provider.config

    slug = spec.get("slug") or name
    display_name = spec.get("displayName") or name
    protocol = spec.get("protocol") or PROTOCOL_OAUTH2
    scopes = spec.get("scopes") or DEFAULT_SCOPES

    try:
        bao = provider.openbao()
        ak = provider.authentik(bao)
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, str(exc), generation)

    # Shared Authentik references.
    try:
        authz_pk = ak.flow_pk(cfg.authorization_flow_slug)
        inval_pk = ak.flow_pk(cfg.invalidation_flow_slug)
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, str(exc), generation)

    if not authz_pk or not inval_pk:
        fail(
            patch,
            status,
            conditions.REASON_DEPENDENCY_NOT_MET,
            "authorization/invalidation flows not found in Authentik",
            generation,
        )

    # Resolve requested scopes to property-mapping pks (skip unresolved). Both
    # protocols forward these claims.
    try:
        mapping_pks = []

        for scope in scopes:
            pk = ak.ensure_scope_mapping(scope)

            if pk:
                mapping_pks.append(pk)
            else:
                logger.info(f"Scope {scope!r} not found in Authentik; skipping")
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, str(exc), generation)

    if protocol == PROTOCOL_PROXY:
        provider_pk = _reconcile_proxy(
            ak,
            cfg,
            spec,
            slug,
            authz_pk,
            inval_pk,
            mapping_pks,
            patch,
            status,
            generation,
        )
        client_id = None
        openbao_path = None
    else:
        provider_pk, client_id, openbao_path = _reconcile_oauth2(
            ak,
            cfg,
            bao,
            spec,
            slug,
            authz_pk,
            inval_pk,
            mapping_pks,
            patch,
            status,
            generation,
        )

    # Application (with metadata) + any declared Authentik groups (shared).
    # Membership is managed in Authentik; consuming apps reference these group names
    # in their SSO RBAC rules (e.g. an Argo Workflows group-to-ServiceAccount mapping).
    try:
        backchannel_pks = []

        for bp in spec.get("backchannelProviders") or []:
            pk = ak.find_provider_pk(bp)

            if pk:
                backchannel_pks.append(pk)
            else:
                logger.info(f"Backchannel provider {bp!r} not found in Authentik; skipping")

        ak.ensure_application(
            ApplicationSpec(
                slug=slug,
                name=display_name,
                provider_pk=provider_pk,
                group=spec.get("group") or "",
                icon=spec.get("icon") or "",
                description=spec.get("description") or "",
                publisher=spec.get("publisher") or "",
                launch_url=spec.get("launchUrl") or "",
                backchannel_provider_pks=backchannel_pks,
            )
        )

        for group_name in spec.get("groups") or []:
            ak.ensure_group(group_name)
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, str(exc), generation)

    # Publish credentials into the app namespace via External Secrets (OAuth2 only;
    # a proxy provider's credentials are generated and owned by Authentik).
    if protocol != PROTOCOL_PROXY:
        target = (spec.get("credentials") or {}).get("targetSecret")

        if target:
            try:
                _publish_credentials(
                    target,
                    meta,
                    namespace,
                    openbao_path,
                    cfg.cluster_secret_store,
                )
            except Exception as exc:  # noqa: BLE001
                fail(
                    patch,
                    status,
                    conditions.REASON_BACKEND_ERROR,
                    f"publishing credentials: {exc}",
                    generation,
                )

    patch.status["slug"] = slug
    patch.status["protocol"] = protocol
    patch.status["providerID"] = str(provider_pk)

    if openbao_path:
        patch.status["openbaoPath"] = openbao_path

    if client_id:
        patch.status["clientID"] = client_id

    conditions.mark_ready(patch, status, f"SSO application {slug!r} reconciled", generation)


def _reconcile_oauth2(
    ak, cfg, bao, spec, slug, authz_pk, inval_pk, mapping_pks, patch, status, generation
) -> tuple[int, str, str]:
    """Reconcile an OAuth2 provider; return (provider_pk, client_id, openbao_path)."""
    if not (spec.get("redirectUris") or []):
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "redirectUris is required for protocol oauth2",
            generation,
        )

    # The credential path defaults to the slug.
    openbao_path = (spec.get("credentials") or {}).get("openbaoPath") or slug

    # Credentials: generate once in OpenBao, never overwrite.
    try:
        creds, _ = bao.ensure_keys(
            openbao_path,
            ["client-id", "client-secret"],
            lambda key: secretgen.random_string(
                secretgen.CLIENT_ID_LENGTH if key == "client-id" else secretgen.CLIENT_SECRET_LENGTH
            ),
        )
    except Exception as exc:  # noqa: BLE001
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"storing credentials: {exc}",
            generation,
        )

    try:
        signing_pk = ak.signing_key_pk(cfg.signing_key_name)

        provider_pk = ak.ensure_oauth2_provider(
            OAuth2ProviderSpec(
                name=slug,
                authorization_flow_pk=authz_pk,
                invalidation_flow_pk=inval_pk,
                client_id=creds["client-id"],
                client_secret=creds["client-secret"],
                redirect_uris=list(spec.get("redirectUris") or []),
                property_mapping_pks=mapping_pks,
                signing_key_pk=signing_pk,
            )
        )
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, str(exc), generation)

    return provider_pk, creds["client-id"], openbao_path


def _reconcile_proxy(
    ak, cfg, spec, slug, authz_pk, inval_pk, mapping_pks, patch, status, generation
) -> int:
    """Reconcile a proxy provider and attach it to an outpost; return provider_pk."""
    proxy = spec.get("proxy") or {}
    external_host = proxy.get("externalHost")

    if not external_host:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "proxy.externalHost is required for protocol proxy",
            generation,
        )

    mode_key = proxy.get("mode") or "forwardSingle"
    mode = PROXY_MODES.get(mode_key)

    if not mode:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            f"unknown proxy.mode {mode_key!r}",
            generation,
        )

    try:
        authn_pk = ak.flow_pk(cfg.authentication_flow_slug)

        provider_pk = ak.ensure_proxy_provider(
            ProxyProviderSpec(
                name=slug,
                authorization_flow_pk=authz_pk,
                invalidation_flow_pk=inval_pk,
                authentication_flow_pk=authn_pk,
                external_host=external_host,
                mode=mode,
                cookie_domain=proxy.get("cookieDomain") or "",
                internal_host=proxy.get("internalHost") or "",
                skip_path_regex=proxy.get("skipPathRegex") or "",
                property_mapping_pks=mapping_pks,
            )
        )
        # For domain-level forward auth, externalHost is the public Authentik URL, so
        # point the outpost's browser URL at it (otherwise it redirects to localhost).
        browser_host = external_host if mode == "forward_domain" else ""

        ak.ensure_outpost_provider(
            proxy.get("outpost") or DEFAULT_OUTPOST,
            provider_pk,
            browser_host,
        )
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, str(exc), generation)

    return provider_pk


@kopf.on.delete(GROUP, VERSION, PLURAL)
def finalize(spec, status, name, logger, **_) -> None:
    """Best-effort cleanup of the Authentik application and provider.

    Never raises: an unreachable backend must not block deletion of the CR. The
    OpenBao credentials are intentionally left in place so a recreated
    application reuses the same client id/secret.
    """
    slug = status.get("slug") or spec.get("slug") or name
    provider_id = status.get("providerID")
    protocol = status.get("protocol") or spec.get("protocol") or PROTOCOL_OAUTH2

    try:
        provider = state.provider()
        bao = provider.openbao()
        ak = provider.authentik(bao)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Backend unavailable during finalize; leaving backend objects: {exc}")
        return

    try:
        ak.delete_application(slug)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed deleting Authentik application {slug!r} during finalize: {exc}")

    if provider_id:
        pk = int(str(provider_id).strip())

        if protocol == PROTOCOL_PROXY:
            outpost = (spec.get("proxy") or {}).get("outpost") or DEFAULT_OUTPOST

            try:
                ak.remove_outpost_provider(outpost, pk)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed detaching provider {pk} from outpost during finalize: {exc}")
            try:
                ak.delete_proxy_provider(pk)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed deleting Authentik proxy provider {pk}: {exc}")
        else:
            try:
                ak.delete_provider(pk)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Failed deleting Authentik provider {provider_id!r} during finalize: {exc}"
                )


def _publish_credentials(target, meta, namespace, openbao_path, store) -> None:
    """Create/update an ExternalSecret delivering the credentials into the target Secret."""
    ns = target.get("namespace") or namespace

    # Own the ExternalSecret only when it lives in the SSOApplication's namespace,
    # so it is garbage-collected with the CR (cross-namespace ownership is invalid).
    owner_refs: list[dict] = []

    if ns == namespace:
        owner_refs = [
            {
                "apiVersion": f"{GROUP}/{VERSION}",
                "kind": "SSOApplication",
                "name": meta["name"],
                "uid": meta["uid"],
                "controller": True,
                "blockOwnerDeletion": True,
            }
        ]

    externalsecrets.apply(
        externalsecrets.Spec(
            name=target["name"],
            namespace=ns,
            secret_name=target["name"],
            store_name=store,
            remote_path=openbao_path,
            client_id_key=target.get("clientIDKey") or "client-id",
            client_secret_key=target.get("clientSecretKey") or "client-secret",
            labels={"app.kubernetes.io/managed-by": "adomi-platform-controller"},
            owner_references=owner_refs,
        )
    )
