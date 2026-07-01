"""The OIDC descriptor the platform publishes for each SSOApplication.

Identity is a capability like any other: the SSOApplication reconciler registers the
app in Authentik and delivers a credential Secret. To keep downstream apps loosely
coupled — depending only on a standard OIDC descriptor, never on Authentik internals
or hand-copied URLs — the controller also writes the full, standard OIDC metadata into
that same Secret. Apps then self-configure from conventional keys (or do OIDC discovery
from ``issuer`` / ``discovery-url``), exactly as they would against any IdP.

Authentik's per-application endpoints are a deterministic function of the public
Authentik URL and the application slug, so the descriptor needs no live lookup.
"""

from __future__ import annotations

DEFAULT_SCOPES = ["openid", "email", "profile"]


def descriptor(authority: str, slug: str, scopes: list[str] | None = None) -> dict[str, str]:
    """Build the standard OIDC descriptor for an Authentik application (pure).

    ``authority`` is the public Authentik base URL (e.g. ``https://auth.example.com``,
    no trailing slash); ``slug`` is the Authentik application slug. Keys are hyphenated
    to match the existing ``client-id`` / ``client-secret`` Secret keys; values mirror
    the OpenID Provider Metadata (issuer, the three shared endpoints, the per-app
    discovery doc and JWKS, and the end-session endpoint).
    """
    authority = authority.rstrip("/")
    app = f"{authority}/application/o/{slug}"

    return {
        "issuer": f"{app}/",
        "discovery-url": f"{app}/.well-known/openid-configuration",
        "authorization-endpoint": f"{authority}/application/o/authorize/",
        "token-endpoint": f"{authority}/application/o/token/",
        "userinfo-endpoint": f"{authority}/application/o/userinfo/",
        "jwks-uri": f"{app}/jwks/",
        "end-session-endpoint": f"{app}/end-session/",
        "scopes": " ".join(scopes or DEFAULT_SCOPES),
    }


def descriptor_values(
    authority: str,
    slug: str,
    *,
    client_id: str,
    secret: str,
    scopes: list[str] | None = None,
    client_secret_key: str = "client-secret",
) -> dict:
    """The OIDC descriptor shaped for injection into a chart's Helm values.

    Same metadata as :func:`descriptor` but camelCased (values convention, so charts
    read ``.Values.oidc.discoveryUrl`` without ``index``), plus the resolved
    ``clientId`` and the delivered ``secret`` name + key so a chart can ``secretKeyRef``
    the client-secret. Charts that need runtime config from a ConfigMap (which cannot
    reference Secret values) render the URLs + clientId from here and secretKeyRef only
    the client-secret.
    """
    d = descriptor(authority, slug, scopes)

    return {
        "issuer": d["issuer"],
        "discoveryUrl": d["discovery-url"],
        "authorizationEndpoint": d["authorization-endpoint"],
        "tokenEndpoint": d["token-endpoint"],
        "userinfoEndpoint": d["userinfo-endpoint"],
        "jwksUri": d["jwks-uri"],
        "endSessionEndpoint": d["end-session-endpoint"],
        "scopes": d["scopes"],
        "clientId": client_id,
        "secret": secret,
        "clientSecretKey": client_secret_key,
    }
