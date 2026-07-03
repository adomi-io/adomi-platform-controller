"""Application endpoints (under a client's environment)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import specs
from ..cluster import ClusterReader
from ..config import Settings, get_settings
from ..deps import get_reader, get_service
from ..models import ApplicationSpec, ResourceStatus, WriteResult
from ..service import ClientService
from ._common import commit, get_status, list_status, remove, client_ns

router = APIRouter(
    prefix="/clients/{client}/environments/{environment}/applications",
    tags=["applications"],
)

PLURAL = "applications"


@router.get("", response_model=list[ResourceStatus])
def list_applications(
    client: str,
    environment: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> list[ResourceStatus]:
    return list_status(
        reader,
        PLURAL,
        namespace=client_ns(settings, client),
        where=lambda s: (s.spec.get("environmentRef") or {}).get("name") == environment,
    )


@router.put("/{name}", response_model=WriteResult)
def put_application(
    client: str,
    environment: str,
    name: str,
    body: ApplicationSpec,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    spec = specs.application_spec(
        environment=environment,
        type=body.type,
        display_name=body.display_name,
        databases=body.databases,
        sso=body.sso,
        env=body.env,
        replicas=body.replicas,
        host=body.host,
        values=body.values,
        source=body.source.model_dump() if body.source else None,
    )

    return commit(service, client, PLURAL, name, spec)


@router.get("/{name}", response_model=ResourceStatus)
def get_application(
    client: str,
    environment: str,
    name: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> ResourceStatus:
    return get_status(reader, settings, client, PLURAL, name)


@router.delete("/{name}", response_model=WriteResult)
def delete_application(
    client: str,
    environment: str,
    name: str,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    return remove(service, client, PLURAL, name)
