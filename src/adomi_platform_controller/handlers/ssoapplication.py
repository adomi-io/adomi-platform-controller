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
from ..authentik import OAuth2ProviderSpec
from ._common import fail

GROUP = "identity.adomi.io"
VERSION = "v1alpha1"
PLURAL = "ssoapplications"

# Requested when an SSOApplication does not declare any scopes.
DEFAULT_SCOPES = ["openid", "profile", "email", "groups"]


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def reconcile(spec, meta, status, patch, name, namespace, logger, **_) -> None:
    generation = meta.get("generation", 0)
    provider = state.provider()
    cfg = provider.config

    slug = spec.get("slug") or name
    display_name = spec.get("displayName") or name
    scopes = spec.get("scopes") or DEFAULT_SCOPES

    credentials = spec.get("credentials") or {}
    # The credential path defaults to the slug.
    openbao_path = credentials.get("openbaoPath") or slug

    try:
        bao = provider.openbao()
        ak = provider.authentik(bao)
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, str(exc), generation)

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

    try:
        signing_pk = ak.signing_key_pk(cfg.signing_key_name)

        # Resolve requested scopes to property-mapping pks (skip unresolved).
        mapping_pks = []
        for scope in scopes:
            pk = ak.ensure_scope_mapping(scope)
            if pk:
                mapping_pks.append(pk)
            else:
                logger.info(f"Scope {scope!r} not found in Authentik; skipping")

        # Provider and application.
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
        ak.ensure_application(slug, display_name, provider_pk)

        # Ensure any declared Authentik groups exist. Membership is managed in
        # Authentik; consuming apps reference these group names in their SSO RBAC
        # rules (e.g. an Argo Workflows group-to-ServiceAccount mapping).
        for group_name in spec.get("groups") or []:
            ak.ensure_group(group_name)
    except Exception as exc:  # noqa: BLE001
        fail(patch, status, conditions.REASON_BACKEND_ERROR, str(exc), generation)

    # Publish credentials into the app namespace via External Secrets.
    target = credentials.get("targetSecret")
    if target:
        try:
            _publish_credentials(target, meta, namespace, openbao_path, cfg.cluster_secret_store)
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"publishing credentials: {exc}",
                generation,
            )

    patch.status["slug"] = slug
    patch.status["providerID"] = str(provider_pk)
    patch.status["openbaoPath"] = openbao_path
    patch.status["clientID"] = creds["client-id"]
    conditions.mark_ready(patch, status, f"SSO application {slug!r} reconciled", generation)


@kopf.on.delete(GROUP, VERSION, PLURAL)
def finalize(spec, status, name, logger, **_) -> None:
    """Best-effort cleanup of the Authentik application and provider.

    Never raises: an unreachable backend must not block deletion of the CR. The
    OpenBao credentials are intentionally left in place so a recreated
    application reuses the same client id/secret.
    """
    slug = status.get("slug") or spec.get("slug") or name
    provider_id = status.get("providerID")

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
        try:
            ak.delete_provider(int(str(provider_id).strip()))
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
