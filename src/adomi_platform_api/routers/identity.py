"""Who can reach an application (Authentik-backed, imperative like secrets).

One Authentik group per app (``<prefix><runtime-namespace>-<app>``) gates
access: while the group is bound to the app's Authentik application(s), only
its members get through; with no members the binding is dropped and the app is
open to every signed-in user again. The app -> Authentik link comes from the
SSOApplication CRs the app's chart emitted (their reconciled slug).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..cluster import ClusterError, ClusterReader
from ..config import Settings, get_settings
from ..deps import get_identity, get_reader
from ..identity import AuthentikAdmin, IdentityError
from ..models import AccessState, AccessUser

router = APIRouter(tags=["identity"])

IDENTITY_GROUP = "identity.adomi.io"


@router.get("/identity/users", response_model=list[AccessUser])
def list_users(
    search: str = Query(default="", description="Filter by username / name / email."),
    identity: AuthentikAdmin = Depends(get_identity),
) -> list[AccessUser]:
    try:
        return [AccessUser(**u) for u in identity.list_users(search=search)]
    except IdentityError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


def _truncate_label(value: str) -> str:
    return value[:63].rstrip("-")


def _release(client: str, environment: str, app: str) -> tuple[str, str]:
    """(runtime namespace, helm release) for an application."""
    namespace = _truncate_label(f"{client}-{environment}")

    return namespace, _truncate_label(f"{namespace}-{app}")


def _sso_slugs(reader: ClusterReader, namespace: str, release: str) -> list[str]:
    """The app's Authentik application slugs, from its SSOApplication CRs."""
    try:
        items = reader.list(
            "ssoapplications",
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={release}",
            group=IDENTITY_GROUP,
        )
    except ClusterError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    slugs = []
    for obj in items:
        slug = (
            (obj.get("status") or {}).get("slug")
            or (obj.get("spec") or {}).get("slug")
            or (obj.get("metadata") or {}).get("name")
        )
        if slug:
            slugs.append(slug)

    return slugs


def _access_path_deps(client: str, environment: str, name: str, settings: Settings):
    namespace, release = _release(client, environment, name)

    return namespace, release, settings.access_group_prefix + release


access = APIRouter(
    prefix="/clients/{client}/environments/{environment}/applications/{name}/access",
    tags=["identity"],
)


@access.get("", response_model=AccessState)
def get_access(
    client: str,
    environment: str,
    name: str,
    reader: ClusterReader = Depends(get_reader),
    identity: AuthentikAdmin = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> AccessState:
    namespace, release, group_name = _access_path_deps(client, environment, name, settings)
    slugs = _sso_slugs(reader, namespace, release)

    if not slugs:
        return AccessState(available=False, reason="no_sso", group=group_name)

    try:
        group = identity.find_group(group_name)
        users = identity.group_members(group) if group else []

        restricted = False
        if group:
            for slug in slugs:
                app = identity.application_by_slug(slug)
                if app and any(
                    b.get("group") == group.get("pk") for b in identity.group_bindings(app["pk"])
                ):
                    restricted = True
                    break
    except IdentityError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return AccessState(
        available=True,
        mode="restricted" if restricted else "everyone",
        group=group_name,
        applications=slugs,
        users=[AccessUser(**u) for u in users],
    )


@access.put("/{user_pk}", response_model=AccessState)
def grant_access(
    client: str,
    environment: str,
    name: str,
    user_pk: int,
    reader: ClusterReader = Depends(get_reader),
    identity: AuthentikAdmin = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> AccessState:
    namespace, release, group_name = _access_path_deps(client, environment, name, settings)
    slugs = _sso_slugs(reader, namespace, release)

    if not slugs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The application has no SSO registration; there is nothing to gate.",
        )

    try:
        apps = [identity.application_by_slug(slug) for slug in slugs]
        apps = [a for a in apps if a]
        if not apps:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "The application is not registered in Authentik yet (still provisioning?).",
            )

        group = identity.ensure_group(group_name)
        identity.add_member(group["pk"], user_pk)
        # Binding AFTER membership: the moment the gate exists, the granted
        # user is already inside it.
        for app in apps:
            identity.ensure_binding(app["pk"], group["pk"])
    except IdentityError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return get_access(client, environment, name, reader, identity, settings)


@access.delete("/{user_pk}", response_model=AccessState)
def revoke_access(
    client: str,
    environment: str,
    name: str,
    user_pk: int,
    reader: ClusterReader = Depends(get_reader),
    identity: AuthentikAdmin = Depends(get_identity),
    settings: Settings = Depends(get_settings),
) -> AccessState:
    namespace, release, group_name = _access_path_deps(client, environment, name, settings)
    slugs = _sso_slugs(reader, namespace, release)

    try:
        group = identity.find_group(group_name)
        if group:
            identity.remove_member(group["pk"], user_pk)
            group = identity.find_group(group_name)  # re-read membership

            if group and not identity.group_members(group):
                # Last member gone: drop the gate so the app opens up again
                # (an empty bound group would lock EVERYONE out).
                for slug in slugs:
                    app = identity.application_by_slug(slug)
                    if app:
                        identity.remove_binding(app["pk"], group["pk"])
    except IdentityError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return get_access(client, environment, name, reader, identity, settings)
