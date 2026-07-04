"""FastAPI dependencies: the git writer + client service, wired from settings."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

from .cluster import ClusterReader
from .config import Settings, get_settings
from .git import ForgejoWriter, GitWriter, Readiness
from .secrets import ScopedSecretsStore
from .service import ClientService


def get_writer(settings: Settings = Depends(get_settings)) -> GitWriter:
    return ForgejoWriter(
        settings.forgejo_url,
        settings.forgejo_token,
        settings.forgejo_org,
        default_branch=settings.git_default_branch,
        timeout=settings.http_timeout,
        verify=settings.forgejo_verify_tls,
    )


def get_service(
    settings: Settings = Depends(get_settings),
    writer: GitWriter = Depends(get_writer),
) -> ClientService:
    return ClientService(
        writer,
        namespace_prefix=settings.client_namespace_prefix,
        managed_by=settings.managed_by,
        git_mode=settings.git_mode,
    )


def get_secrets_store(settings: Settings = Depends(get_settings)) -> ScopedSecretsStore:
    return ScopedSecretsStore(
        settings.openbao_addr,
        settings.openbao_mount,
        token=settings.openbao_token,
        auth_mount=settings.openbao_auth_mount,
        role=settings.openbao_role,
    )


def check_backend_ready(writer: GitWriter = Depends(get_writer)) -> Readiness:
    return writer.check_ready()


@lru_cache
def _reader() -> ClusterReader:
    return ClusterReader()


def get_reader() -> ClusterReader:
    return _reader()
