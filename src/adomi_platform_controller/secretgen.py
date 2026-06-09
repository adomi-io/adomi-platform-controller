"""Cryptographically random alphanumeric credentials for OAuth client ids and
secrets."""

from __future__ import annotations

import secrets

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Default credential lengths.
CLIENT_ID_LENGTH = 40
CLIENT_SECRET_LENGTH = 128


def random_string(n: int) -> str:
    """Return a cryptographically random alphanumeric string of length n."""
    if n <= 0:
        return ""
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))
