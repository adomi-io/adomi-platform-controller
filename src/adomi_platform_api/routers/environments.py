"""Environment endpoints (under a client)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import specs
from ..cluster import ClusterReader
from ..config import Settings, get_settings
from ..deps import get_reader, get_service
from ..models import ResourceStatus, EnvironmentSpec, WriteResult
from ..service import ClientService
from ._common import commit, get_status, list_status, remove, client_ns

router = APIRouter(prefix="/clients/{client}/environments", tags=["environments"])

PLURAL = "environments"


@router.get("", response_model=list[ResourceStatus])
def list_environments(
    client: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> list[ResourceStatus]:
    return list_status(reader, PLURAL, namespace=client_ns(settings, client))


@router.put("/{name}", response_model=WriteResult)
def put_environment(
    client: str,
    name: str,
    body: EnvironmentSpec,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    spec = specs.environment_spec(
        client=client,
        environment_class=body.environment_class,
        display_name=body.display_name,
    )

    return commit(service, client, PLURAL, name, spec)


@router.get("/{name}", response_model=ResourceStatus)
def get_environment(
    client: str,
    name: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> ResourceStatus:
    return get_status(reader, settings, client, PLURAL, name)


@router.delete("/{name}", response_model=WriteResult)
def delete_environment(
    client: str,
    name: str,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    return remove(service, client, PLURAL, name)
