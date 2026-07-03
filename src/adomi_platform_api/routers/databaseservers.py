"""Database server endpoints (under a client)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import specs
from ..cluster import ClusterReader
from ..config import Settings, get_settings
from ..deps import get_reader, get_service
from ..models import DatabaseServerSpec, ResourceStatus, WriteResult
from ..service import ClientService
from ._common import commit, get_status, list_status, remove, client_ns

router = APIRouter(prefix="/clients/{client}/databaseservers", tags=["databaseservers"])

PLURAL = "databaseservers"


@router.get("", response_model=list[ResourceStatus])
def list_database_servers(
    client: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> list[ResourceStatus]:
    return list_status(reader, PLURAL, namespace=client_ns(settings, client))


@router.put("/{name}", response_model=WriteResult)
def put_database_server(
    client: str,
    name: str,
    body: DatabaseServerSpec,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    spec = specs.databaseserver_spec(
        engine=body.engine,
        mode=body.mode,
        storage=body.storage,
        storage_class=body.storage_class,
        instances=body.instances,
        environment=body.environment,
        host=body.host,
        port=body.port,
        admin_user=body.admin_user,
        admin_openbao_path=body.admin_openbao_path,
        ssl_mode=body.ssl_mode,
    )

    return commit(service, client, PLURAL, name, spec)


@router.get("/{name}", response_model=ResourceStatus)
def get_database_server(
    client: str,
    name: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> ResourceStatus:
    return get_status(reader, settings, client, PLURAL, name)


@router.delete("/{name}", response_model=WriteResult)
def delete_database_server(
    client: str,
    name: str,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    return remove(service, client, PLURAL, name)
