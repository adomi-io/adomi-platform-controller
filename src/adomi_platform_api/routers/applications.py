"""Application endpoints (under a client's workspace)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import specs
from ..cluster import ClusterReader
from ..config import Settings, get_settings
from ..deps import get_reader, get_service
from ..models import ApplicationSpec, ResourceStatus, WriteResult
from ..service import TenantService
from ._common import commit, get_status, list_status, remove, tenant_ns

router = APIRouter(
    prefix="/clients/{client}/workspaces/{workspace}/applications",
    tags=["applications"],
)

PLURAL = "applications"


@router.get("", response_model=list[ResourceStatus])
def list_applications(
    client: str,
    workspace: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> list[ResourceStatus]:
    return list_status(
        reader,
        PLURAL,
        namespace=tenant_ns(settings, client),
        where=lambda s: (s.spec.get("workspaceRef") or {}).get("name") == workspace,
    )


@router.put("/{name}", response_model=WriteResult)
def put_application(
    client: str,
    workspace: str,
    name: str,
    body: ApplicationSpec,
    service: TenantService = Depends(get_service),
) -> WriteResult:
    spec = specs.application_spec(
        workspace=workspace,
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
    workspace: str,
    name: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> ResourceStatus:
    return get_status(reader, settings, client, PLURAL, name)


@router.delete("/{name}", response_model=WriteResult)
def delete_application(
    client: str,
    workspace: str,
    name: str,
    service: TenantService = Depends(get_service),
) -> WriteResult:
    return remove(service, client, PLURAL, name)
