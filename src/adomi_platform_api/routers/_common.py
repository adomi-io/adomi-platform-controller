"""Shared router helpers: commit/remove (git) + status reads (cluster)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, status

from adomi_platform_schema import SchemaError, client_namespace

from ..cluster import ClusterError, ClusterReader
from ..config import Settings
from ..git import GitError
from ..models import ResourceStatus, WriteResult
from ..service import ClientService


# --- writes (git) ---------------------------------------------------------------
def commit(service: ClientService, client, plural, name, spec, labels=None) -> WriteResult:
    try:
        return WriteResult(**service.commit(client, plural, name, spec, labels=labels))
    except SchemaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except GitError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


def remove(service: ClientService, client, plural, name) -> WriteResult:
    try:
        return WriteResult(**service.remove(client, plural, name))
    except SchemaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except GitError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


# --- reads (cluster status) -----------------------------------------------------
def client_ns(settings: Settings, client: str) -> str:
    """The namespace a Client's CRs live in (per settings)."""
    return client_namespace(client, settings.client_namespace_prefix)


def get_status(reader: ClusterReader, settings: Settings, client, plural, name) -> ResourceStatus:
    try:
        obj = reader.get(plural, client_ns(settings, client), name)
    except ClusterError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{plural}/{name} not found")

    return ResourceStatus.from_cr(obj)


def list_status(
    reader: ClusterReader,
    plural: str,
    *,
    namespace: str | None = None,
    where: Callable[[ResourceStatus], bool] | None = None,
) -> list[ResourceStatus]:
    try:
        items = reader.list(plural, namespace=namespace)
    except ClusterError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    out = [ResourceStatus.from_cr(o) for o in items]

    return [s for s in out if where(s)] if where else out
