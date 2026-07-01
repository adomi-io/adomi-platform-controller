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
