"""Registry of integration connectors, keyed by Application.spec.integrations[].type."""

from __future__ import annotations

from typing import Protocol

from ..apptypes import base
from .odoo_mailpit_smtp import OdooMailpitSmtp
from .odoo_superset_datasource import OdooSupersetDatasource


class Connector(Protocol):
    consumer_type: str
    provider_type: str

    def values(self, provider_connection: dict, ctx: base.Ctx) -> dict: ...


_CONNECTORS: dict[str, Connector] = {
    "odoo-mailpit-smtp": OdooMailpitSmtp(),
    "odoo-superset-datasource": OdooSupersetDatasource(),
}


def get(integration_type: str) -> Connector | None:
    """Return the connector for an integration type, or None if unknown."""
    return _CONNECTORS.get(integration_type)
