"""Runtime configuration, loaded from the environment (prefix ``ADOMI_API_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from adomi_platform_schema import DEFAULT_TENANT_NAMESPACE_PREFIX, MANAGED_BY


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADOMI_API_", env_file=".env", extra="ignore")

    # Bearer token producers (Odoo / CLI / partner UIs) authenticate with.
    auth_token: str = ""
    # Local-dev only escape hatch; never set in-cluster.
    allow_anonymous: bool = False

    # Git backend (Forgejo, in-cluster).
    forgejo_url: str = "http://forgejo-http.forgejo.svc.cluster.local:3000"
    forgejo_token: str = ""
    forgejo_org: str = "tenants"
    forgejo_verify_tls: bool = True
    git_default_branch: str = "main"
    # 'commit' (push to the default branch) or 'pr' (commit a branch + open a PR).
    git_mode: str = "commit"
    http_timeout: float = 15.0

    # Namespace each customer's committed CRs land in.
    tenant_namespace_prefix: str = DEFAULT_TENANT_NAMESPACE_PREFIX

    managed_by: str = MANAGED_BY


@lru_cache
def get_settings() -> Settings:
    return Settings()
