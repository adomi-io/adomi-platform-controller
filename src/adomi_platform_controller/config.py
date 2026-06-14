"""Static backend configuration, populated from environment variables.

Defaults match the kubernetes-provisioner conventions so the operator drops into
an existing OpenBao / Authentik / External Secrets setup with no configuration.
The Helm chart sets these env vars from its ``backend`` values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class AuthMode(str, Enum):
    """How the controller authenticates to OpenBao."""

    #: Read a static token from a Kubernetes Secret (the openbao-keys root-token
    #: by default). Simple, but the token is broad; prefer KUBERNETES.
    TOKEN = "token"
    #: Log in with the pod's ServiceAccount JWT via OpenBao kubernetes auth.
    KUBERNETES = "kubernetes"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value is not None and value != "" else default


@dataclass(frozen=True)
class Config:
    # OpenBao.
    openbao_addr: str = "http://openbao.openbao.svc.cluster.local:8200"
    kv_mount: str = "secret"
    auth_mode: AuthMode = AuthMode.TOKEN
    token_secret_namespace: str = "openbao"  # for AuthMode.TOKEN
    token_secret_name: str = "openbao-keys"  # for AuthMode.TOKEN
    token_secret_key: str = "root-token"  # key within the token Secret
    k8s_auth_mount: str = "kubernetes"  # for AuthMode.KUBERNETES
    k8s_auth_role: str = "adomi-platform-controller"  # for AuthMode.KUBERNETES
    jwt_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"

    # Authentik.
    authentik_addr: str = "http://authentik-server.authentik.svc.cluster.local"
    authentik_secret_path: str = "authentik"  # OpenBao KV path holding the API token
    authentik_token_key: str = "bootstrap-token"  # key within that path
    authorization_flow_slug: str = "default-provider-authorization-implicit-consent"
    invalidation_flow_slug: str = "default-provider-invalidation-flow"
    # Flow proxy providers send un-authenticated users through (forward-auth login).
    authentication_flow_slug: str = "default-authentication-flow"
    signing_key_name: str = "authentik Self-signed Certificate"

    # External Secrets.
    cluster_secret_store: str = "openbao"

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables, falling back to defaults."""
        d = cls()  # defaults
        return cls(
            openbao_addr=_env("OPENBAO_ADDR", d.openbao_addr),
            kv_mount=_env("OPENBAO_KV_MOUNT", d.kv_mount),
            auth_mode=AuthMode(_env("OPENBAO_AUTH_MODE", d.auth_mode.value)),
            token_secret_namespace=_env("OPENBAO_TOKEN_SECRET_NAMESPACE", d.token_secret_namespace),
            token_secret_name=_env("OPENBAO_TOKEN_SECRET_NAME", d.token_secret_name),
            token_secret_key=_env("OPENBAO_TOKEN_SECRET_KEY", d.token_secret_key),
            k8s_auth_mount=_env("OPENBAO_KUBERNETES_AUTH_MOUNT", d.k8s_auth_mount),
            k8s_auth_role=_env("OPENBAO_KUBERNETES_AUTH_ROLE", d.k8s_auth_role),
            jwt_path=_env("OPENBAO_JWT_PATH", d.jwt_path),
            authentik_addr=_env("AUTHENTIK_ADDR", d.authentik_addr),
            authentik_secret_path=_env("AUTHENTIK_SECRET_PATH", d.authentik_secret_path),
            authentik_token_key=_env("AUTHENTIK_TOKEN_KEY", d.authentik_token_key),
            authorization_flow_slug=_env("AUTHENTIK_AUTHORIZATION_FLOW", d.authorization_flow_slug),
            invalidation_flow_slug=_env("AUTHENTIK_INVALIDATION_FLOW", d.invalidation_flow_slug),
            authentication_flow_slug=_env(
                "AUTHENTIK_AUTHENTICATION_FLOW", d.authentication_flow_slug
            ),
            signing_key_name=_env("AUTHENTIK_SIGNING_KEY_NAME", d.signing_key_name),
            cluster_secret_store=_env("CLUSTER_SECRET_STORE", d.cluster_secret_store),
        )
