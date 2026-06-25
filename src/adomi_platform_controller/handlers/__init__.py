"""Reconcile handlers for the platform CRDs.

Importing this package registers all @kopf.on.* handlers.
"""

from __future__ import annotations

from . import (  # noqa: F401
    application,
    applicationtype,
    client,
    database,
    databaseserver,
    domain,
    gitrepository,
    odoo_sync,
    organization,
    snapshot,
    ssoapplication,
    workspace,
)
