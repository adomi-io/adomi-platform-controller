"""GitRepository endpoints (under a client — source repos for builds/previews)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import specs
from ..cluster import ClusterReader
from ..config import Settings, get_settings
from ..deps import get_reader, get_service
from ..models import GitRepositorySpec, ResourceStatus, WriteResult
from ..service import ClientService
from ._common import commit, get_status, list_status, remove, client_ns

router = APIRouter(prefix="/clients/{client}/gitrepositories", tags=["gitrepositories"])

PLURAL = "gitrepositories"


@router.get("", response_model=list[ResourceStatus])
def list_gitrepositories(
    client: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> list[ResourceStatus]:
    return list_status(reader, PLURAL, namespace=client_ns(settings, client))


@router.put("/{name}", response_model=WriteResult)
def put_gitrepository(
    client: str,
    name: str,
    body: GitRepositorySpec,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    spec = specs.gitrepository_spec(
        url=body.url,
        default_branch=body.default_branch,
        credentials_secret=body.credentials_secret,
        preview=body.preview.model_dump() if body.preview else None,
    )

    return commit(service, client, PLURAL, name, spec)


@router.get("/{name}", response_model=ResourceStatus)
def get_gitrepository(
    client: str,
    name: str,
    reader: ClusterReader = Depends(get_reader),
    settings: Settings = Depends(get_settings),
) -> ResourceStatus:
    return get_status(reader, settings, client, PLURAL, name)


@router.delete("/{name}", response_model=WriteResult)
def delete_gitrepository(
    client: str,
    name: str,
    service: ClientService = Depends(get_service),
) -> WriteResult:
    return remove(service, client, PLURAL, name)
