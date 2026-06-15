"""Registry of app-type adapters, keyed by ApplicationType.spec.adapter."""

from __future__ import annotations

from .base import Adapter
from .generic import GenericAdapter
from .mailpit import MailpitAdapter
from .odoo import OdooAdapter
from .superset import SupersetAdapter

GENERIC = "generic"

_ADAPTERS: dict[str, Adapter] = {
    "odoo": OdooAdapter(),
    "superset": SupersetAdapter(),
    "mailpit": MailpitAdapter(),
    GENERIC: GenericAdapter(),
}


def get(name: str) -> Adapter:
    """Return the adapter for an ApplicationType.spec.adapter (falls back to generic)."""
    return _ADAPTERS.get(name or GENERIC, _ADAPTERS[GENERIC])
