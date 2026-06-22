"""Process-wide singletons shared between the operator startup and the handlers.

The backend Provider is created once at startup (after the Kubernetes client is
configured) and read by every reconcile handler.
"""

from __future__ import annotations

from .backend import Provider

_provider: Provider | None = None


def set_provider(provider: Provider) -> None:
    global _provider
    _provider = provider


def provider() -> Provider:
    if _provider is None:
        raise RuntimeError("backend Provider not initialised; operator startup did not run")

    return _provider
