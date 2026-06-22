"""Bearer-token auth for producers (Odoo / CLI / partner UIs)."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from .config import get_settings


def require_token(authorization: str = Header(default="")) -> None:
    """Validate the ``Authorization: Bearer <token>`` header (constant-time)."""
    settings = get_settings()

    if settings.allow_anonymous:
        return

    if not settings.auth_token:
        # Misconfiguration: refuse rather than serve unauthenticated by accident.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "API auth token not configured",
        )

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not hmac.compare_digest(token, settings.auth_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token")
