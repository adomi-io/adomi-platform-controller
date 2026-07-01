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
    # Public, browser-facing Authentik base URL. The OIDC descriptor the controller
    # publishes into each SSO Secret (issuer / endpoints) must use this — the id_token
    # issuer and the browser redirects are the public URL, never the in-cluster addr.
    # Falls back to https://auth.<baseDomain>.
    authentik_public_host: str = ""  # e.g. auth.example.com

    def resolved_authentik_url(self) -> str:
        """The public Authentik base URL (https, no trailing slash) for OIDC discovery."""
        host = self.authentik_public_host or (
            f"auth.{self.base_domain}" if self.base_domain else ""
        )

        if not host:
            return ""

        if host.startswith(("http://", "https://")):
            return host.rstrip("/")

        return f"https://{host}"

    authorization_flow_slug: str = "default-provider-authorization-implicit-consent"
    invalidation_flow_slug: str = "default-provider-invalidation-flow"
    # Flow proxy providers send un-authenticated users through (forward-auth login).
    authentication_flow_slug: str = "default-authentication-flow"
    signing_key_name: str = "authentik Self-signed Certificate"

    # External Secrets.
    cluster_secret_store: str = "openbao"

    # Databases. A Database's login-role password is generated once into OpenBao under
    # "<prefix>/<server>/<user>" (so apps sharing a role share its password) and the
    # provisioning Job runs this image (it must ship psql).
    database_credentials_path: str = "databases"  # OpenBao KV prefix for role passwords
    database_password_length: int = 32
    db_provision_image: str = "postgres:16"  # image with psql for the provisioning Job

    # Argo CD. The Application engine creates an Argo CD Application per app; the
    # chart source comes from each ApplicationType, so only the install location is
    # configured here.
    argocd_namespace: str = "argocd"  # where Application objects live
    argocd_project: str = "default"  # the AppProject apps are placed in

    # Default Odoo container image (the odoo adapter's base image when an Application
    # doesn't build from source / pin a version). The chart tag defaults to its
    # appVersion.
    odoo_image_repository: str = "ghcr.io/adomi-io/odoo"

    # Platform domain. Generated application hostnames are a single DNS label
    # "<app>-<workspace>-<client>.<baseDomain>" (so a *.<baseDomain> wildcard
    # cert/record covers them) unless the application sets spec.ingress.host. Empty
    # means an application must declare a host or supply an Organization base domain.
    base_domain: str = ""

    # Forward-auth. The Traefik middleware (in "<namespace>-<name>@kubernetescrd"
    # form) added to an Odoo Ingress to gate it behind the Authentik outpost when
    # SSO is enabled. Empty disables ingress wiring (the SSOApplication is still
    # created).
    forward_auth_middleware: str = "authentik-authentik@kubernetescrd"

    # Build pipeline. When an Application declares a source, the controller
    # submits an Argo Workflow (from the shipped WorkflowTemplate) that builds the
    # repository image and pushes it to Harbor.
    argo_namespace: str = "argo"  # where Argo Workflows + build secrets live
    build_workflow_template: str = "odoo-image-build"  # WorkflowTemplate name in argo
    build_service_account: str = "odoo-build"  # ServiceAccount the build runs as

    # Harbor registry the built images are pushed to.
    harbor_host: str = ""  # e.g. harbor.example.com; falls back to harbor.<baseDomain>
    harbor_project: str = "previews"  # Harbor project/repository prefix for built images
    harbor_username: str = "admin"  # registry push user
    harbor_secret_path: str = "harbor-app"  # OpenBao KV path holding the push password
    harbor_secret_key: str = "admin-password"  # key within that path

    def resolved_harbor_host(self) -> str:
        """The Harbor host, defaulting to harbor.<baseDomain> when not set."""
        if self.harbor_host:
            return self.harbor_host
        if self.base_domain:
            return f"harbor.{self.base_domain}"
        return ""

    # Preview environments. When a GitRepository enables previews, the controller
    # generates an Argo Events github EventSource + Sensor + webhook Ingress; PR
    # events create/rebuild/destroy preview Workspaces and Applications.
    webhook_host: str = ""  # public webhook host; falls back to hooks.<baseDomain>
    cluster_issuer: str = "letsencrypt-prod"  # cert-manager issuer for the webhook Ingress
    preview_ingress_class: str = "traefik"  # IngressClass for the webhook Ingress
    preview_sensor_service_account: str = "odoo-previews"  # SA the Sensor runs as
    github_api_url: str = "https://api.github.com"  # override for GitHub Enterprise

    def resolved_webhook_host(self) -> str:
        """The webhook host, defaulting to hooks.<baseDomain> when not set."""
        if self.webhook_host:
            return self.webhook_host
        if self.base_domain:
            return f"hooks.{self.base_domain}"
        return ""

    # Database snapshots. A Snapshot dumps an environment's Postgres DB to object
    # storage; an Application can restore (and optionally sanitize) from one.
    s3_endpoint: str = "http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333"
    s3_bucket: str = "platform"  # snapshots are stored under the "snapshots/" prefix
    s3_secret_path: str = "s3"  # OpenBao KV path holding the object-store credentials
    s3_access_key_key: str = "access-key"  # key within that path
    s3_secret_key_key: str = "secret-key"  # key within that path
    snapshot_workflow_template: str = "odoo-db-snapshot"  # WorkflowTemplate in argo
    restore_workflow_template: str = "odoo-db-restore"  # WorkflowTemplate in argo
    snapshot_postgres_image: str = "postgres:16"  # image with pg_dump/pg_restore
    snapshot_awscli_image: str = "amazon/aws-cli:2"  # image with the aws CLI

    # Odoo management portal push. When a platform CR's status changes, the
    # controller POSTs it to the Odoo portal's ingest endpoint so Odoo reflects
    # live state immediately instead of polling. Empty URL disables the push (the
    # portal's fallback cron still reconciles). A shared bearer token (read from
    # OpenBao) authenticates the call and must match the portal's ADOMI_INGEST_TOKEN.
    odoo_notify_url: str = (
        ""  # e.g. http://adomi-platform-management.adomi-platform-management.svc.cluster.local:8069
    )
    odoo_notify_secret_path: str = "adomi-ingest"  # OpenBao KV path holding the token
    odoo_notify_token_key: str = "token"  # key within that path

    def odoo_notify_enabled(self) -> bool:
        """True when status pushes to the Odoo portal are configured."""
        return bool(self.odoo_notify_url)

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
            authentik_public_host=_env("AUTHENTIK_PUBLIC_HOST", d.authentik_public_host),
            authorization_flow_slug=_env("AUTHENTIK_AUTHORIZATION_FLOW", d.authorization_flow_slug),
            invalidation_flow_slug=_env("AUTHENTIK_INVALIDATION_FLOW", d.invalidation_flow_slug),
            authentication_flow_slug=_env(
                "AUTHENTIK_AUTHENTICATION_FLOW", d.authentication_flow_slug
            ),
            signing_key_name=_env("AUTHENTIK_SIGNING_KEY_NAME", d.signing_key_name),
            cluster_secret_store=_env("CLUSTER_SECRET_STORE", d.cluster_secret_store),
            database_credentials_path=_env(
                "DATABASE_CREDENTIALS_PATH", d.database_credentials_path
            ),
            db_provision_image=_env("DB_PROVISION_IMAGE", d.db_provision_image),
            argocd_namespace=_env("ARGOCD_NAMESPACE", d.argocd_namespace),
            argocd_project=_env("ARGOCD_PROJECT", d.argocd_project),
            odoo_image_repository=_env("ODOO_IMAGE_REPOSITORY", d.odoo_image_repository),
            base_domain=_env("PLATFORM_BASE_DOMAIN", d.base_domain),
            forward_auth_middleware=_env("FORWARD_AUTH_MIDDLEWARE", d.forward_auth_middleware),
            argo_namespace=_env("ARGO_NAMESPACE", d.argo_namespace),
            build_workflow_template=_env("BUILD_WORKFLOW_TEMPLATE", d.build_workflow_template),
            build_service_account=_env("BUILD_SERVICE_ACCOUNT", d.build_service_account),
            harbor_host=_env("HARBOR_HOST", d.harbor_host),
            harbor_project=_env("HARBOR_PROJECT", d.harbor_project),
            harbor_username=_env("HARBOR_USERNAME", d.harbor_username),
            harbor_secret_path=_env("HARBOR_SECRET_PATH", d.harbor_secret_path),
            harbor_secret_key=_env("HARBOR_SECRET_KEY", d.harbor_secret_key),
            webhook_host=_env("WEBHOOK_HOST", d.webhook_host),
            cluster_issuer=_env("CLUSTER_ISSUER", d.cluster_issuer),
            preview_ingress_class=_env("PREVIEW_INGRESS_CLASS", d.preview_ingress_class),
            preview_sensor_service_account=_env(
                "PREVIEW_SENSOR_SERVICE_ACCOUNT", d.preview_sensor_service_account
            ),
            github_api_url=_env("GITHUB_API_URL", d.github_api_url),
            s3_endpoint=_env("S3_ENDPOINT", d.s3_endpoint),
            s3_bucket=_env("S3_BUCKET", d.s3_bucket),
            s3_secret_path=_env("S3_SECRET_PATH", d.s3_secret_path),
            s3_access_key_key=_env("S3_ACCESS_KEY_KEY", d.s3_access_key_key),
            s3_secret_key_key=_env("S3_SECRET_KEY_KEY", d.s3_secret_key_key),
            snapshot_workflow_template=_env(
                "SNAPSHOT_WORKFLOW_TEMPLATE", d.snapshot_workflow_template
            ),
            restore_workflow_template=_env(
                "RESTORE_WORKFLOW_TEMPLATE", d.restore_workflow_template
            ),
            snapshot_postgres_image=_env("SNAPSHOT_POSTGRES_IMAGE", d.snapshot_postgres_image),
            snapshot_awscli_image=_env("SNAPSHOT_AWSCLI_IMAGE", d.snapshot_awscli_image),
            odoo_notify_url=_env("ODOO_NOTIFY_URL", d.odoo_notify_url),
            odoo_notify_secret_path=_env("ODOO_NOTIFY_SECRET_PATH", d.odoo_notify_secret_path),
            odoo_notify_token_key=_env("ODOO_NOTIFY_TOKEN_KEY", d.odoo_notify_token_key),
        )
