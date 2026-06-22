"""The versioned API router: one router per controller object, under /v1, authed.

The API speaks the controller's object language (Client, Workspace, Application,
Database, Domain, GitRepository, Snapshot); each router writes its CR to the client's
tenant git repo and reads live status from the cluster.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_token
from . import (
    applications,
    clients,
    databases,
    domains,
    gitrepositories,
    snapshots,
    workspaces,
)

api_router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])

for _module in (clients, domains, databases, workspaces, applications, gitrepositories, snapshots):
    api_router.include_router(_module.router)
