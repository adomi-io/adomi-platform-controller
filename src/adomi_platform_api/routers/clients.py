"""Client endpoints (the client — owns a git repo)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import specs
from ..cluster import ClusterReader
from ..config import Settings, get_settings
from ..deps import get_reader, get_service
from ..models import ClientSpec, ResourceStatus, WriteResult
from ..service import ClientService
from ._common import commit, get_status, list_status, remove

router = APIRouter(prefix="/clients", tags=["clients"])

PLURAL = "clients"


@router.get("", response_model=list[ResourceStatus])
def list_clients(reader: ClusterReader = Depends(get_reader)) -> list[ResourceStatus]:
    # Clients live one-per-client-namespace; list cluster-wide.
    return list_status(reader, PLURAL, namespace=None)


@router.put("/{client}", response_model=WriteResult)
def put_client(
    client: str,
    body: ClientSpec,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    spec = specs.client_spec(
        display_name=body.display_name,
        slug=body.slug,
        organization=body.organization,
    )

    return commit(service, client, PLURAL, client, spec)


@router.get("/{client}", response_model=ResourceStatus)
def get_client(
    client: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> ResourceStatus:
    return get_status(reader, settings, client, PLURAL, client)


@router.delete("/{client}", response_model=WriteResult)
def delete_client(
    client: str,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    return remove(service, client, PLURAL, client)
