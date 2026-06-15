"""The app-type adapter interface and shared value helpers.

An adapter maps the platform's standard, resolved inputs (``Ctx``) into a specific
chart's Helm value shape. Adapters are pure (no cluster access) so they are
unit-testable; the engine resolves the Ctx, runs the adapter, merges the result
with catalog defaults / integration values / user overrides, and creates the Argo
CD Application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..resolve import DbConnection

# Traefik forward-auth header annotation (proxy SSO).
TRAEFIK_MIDDLEWARE_ANNOTATION = "traefik.ingress.kubernetes.io/router.middlewares"


@dataclass
class Ctx:
    """Resolved inputs handed to an adapter."""

    app_name: str
    namespace: str
    host: str
    url: str
    ingress_class_name: str
    longpolling: bool = False
    list_db: bool = True
    image: str = ""  # full image ref (repo:tag) when resolved (e.g. odoo build), else ""
    db: DbConnection | None = None
    sso_protocol: str = ""  # "", "oauth2", "proxy"
    sso_secret: str = ""  # oauth2 client credentials Secret name
    forward_auth_middleware: str = ""
    odoo: dict = field(default_factory=dict)
    replicas: int = 1
    admin_password: dict | None = None
    ingress_tls: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class Adapter(Protocol):
    """Maps a Ctx to chart values and declares the app's connection contract."""

    def helm_values(self, ctx: Ctx) -> dict: ...

    def connection(self, ctx: Ctx) -> dict: ...


def standard_ingress(ctx: Ctx, *, longpolling: bool = False) -> dict:
    """The ingress block used by our charts (odoo, mailpit, generic).

    Adds the Traefik forward-auth middleware annotation when SSO is in proxy mode.
    """
    ingress: dict = {
        "enabled": True,
        "className": ctx.ingress_class_name,
        "hosts": [{"host": ctx.host, "paths": [{"path": "/", "pathType": "Prefix"}]}],
    }
    if ctx.ingress_tls:
        ingress["tls"] = ctx.ingress_tls
    if ctx.sso_protocol == "proxy" and ctx.forward_auth_middleware:
        ingress["annotations"] = {TRAEFIK_MIDDLEWARE_ANNOTATION: ctx.forward_auth_middleware}
    if longpolling:
        ingress["longpolling"] = {"enabled": True}
    return ingress


def existing_secret_db(ctx: Ctx) -> dict:
    """The standard ``database`` block (existingSecret pattern) for our charts."""
    db = ctx.db
    if db is None:
        return {}
    return {
        "host": db.host,
        "port": db.port,
        "name": db.name,
        "user": db.user,
        "existingSecret": db.password_secret_name,
        "existingSecretKey": db.password_secret_key,
        "sslmode": "require",
    }


def image_block(ctx: Ctx) -> dict:
    """Split a resolved ``repo:tag`` image ref into the chart's image block.

    A trailing ``:tag`` only counts when the last ``:`` comes after the last ``/``
    (so a registry ``host:port/repo`` with no tag isn't mistaken for a tag).
    """
    ref = ctx.image
    if not ref:
        return {}
    colon = ref.rfind(":")
    if colon > ref.rfind("/"):
        return {"repository": ref[:colon], "tag": ref[colon + 1 :]}
    return {"repository": ref}
