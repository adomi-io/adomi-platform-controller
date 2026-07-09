"""Runtime configuration, loaded from the environment (prefix ``ADOMI_API_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from adomi_platform_schema import DEFAULT_CLIENT_NAMESPACE_PREFIX, MANAGED_BY


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADOMI_API_", env_file=".env", extra="ignore")

    # Bearer token producers (Odoo / CLI / partner UIs) authenticate with.
    auth_token: str = ""
    # Local-dev only escape hatch; never set in-cluster.
    allow_anonymous: bool = False

    # Git backend (Forgejo, in-cluster).
    forgejo_url: str = "http://forgejo-http.forgejo.svc.cluster.local:3000"
    forgejo_token: str = ""
    forgejo_org: str = "clients"
    forgejo_verify_tls: bool = True
    git_default_branch: str = "main"
    # 'commit' (push to the default branch) or 'pr' (commit a branch + open a PR).
    git_mode: str = "commit"
    http_timeout: float = 15.0

    # Namespace each customer's committed CRs land in.
    client_namespace_prefix: str = DEFAULT_CLIENT_NAMESPACE_PREFIX

    managed_by: str = MANAGED_BY

    # Scoped Secrets (OpenBao KV v2). Secret VALUES go straight here - never git.
    # Auth: openbao_token (dev/tests) or the Kubernetes auth role for the API's
    # ServiceAccount (the same auth mount External Secrets and the controller use).
    openbao_addr: str = ""
    openbao_mount: str = "secret"
    openbao_token: str = ""
    openbao_auth_mount: str = "kubernetes"
    openbao_role: str = "adomi-platform-api"
    # KV prefix for scoped Variables/Secrets; must match the controller's
    # SCOPED_SECRETS_PREFIX.
    scoped_secrets_prefix: str = "scoped"

    # Identity (Authentik): per-app access management. The admin token lives in
    # OpenBao (same path/key the controller uses); the API's OpenBao policy
    # must grant read on it.
    authentik_addr: str = "http://authentik-server.authentik.svc.cluster.local"
    authentik_secret_path: str = "authentik"
    authentik_token_key: str = "bootstrap-token"
    authentik_verify_tls: bool = True
    # One Authentik group per app gates access: <prefix><runtime-namespace>-<app>.
    access_group_prefix: str = "app-access-"

    # Container registry (Harbor): the portal's Images section. Reads go over
    # the in-cluster core service; the admin credential lives in OpenBao (the
    # same path the controller pushes with — the API's OpenBao policy must
    # grant read on it). harbor_host is the PUBLIC registry host image
    # references are shown under (what a docker pull would use).
    harbor_url: str = "http://harbor-core.harbor.svc.cluster.local"
    harbor_host: str = ""
    harbor_project: str = "previews"
    harbor_username: str = "admin"
    harbor_secret_path: str = "harbor-app"
    harbor_secret_key: str = "admin-password"
    harbor_verify_tls: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
