"""Resolves the effective configuration for an Application.

An Application's settings come from four layers, each overriding the one before:

    controller Config defaults -> Organization -> ApplicationType -> Application

This module fetches the referenced Organization / Client / Workspace / ApplicationType
objects and folds them, with the controller Config, into a single ``Effective`` value
the Application engine consumes. The folding logic (``compute``) is pure over plain
dicts so it can be unit-tested without a cluster.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from .config import Config

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"

PLURAL_ORGANIZATIONS = "organizations"
PLURAL_CLIENTS = "clients"
PLURAL_WORKSPACES = "workspaces"
PLURAL_APPLICATIONS = "applications"
PLURAL_APPLICATIONTYPES = "applicationtypes"
PLURAL_GITREPOSITORIES = "gitrepositories"
PLURAL_SNAPSHOTS = "snapshots"
PLURAL_DATABASESERVERS = "databaseservers"
PLURAL_DATABASES = "databases"
PLURAL_DOMAINS = "domains"

# SSOApplication lives in a different API group (identity, not platform).
IDENTITY_GROUP = "identity.adomi.io"
PLURAL_SSOAPPLICATIONS = "ssoapplications"

# Workspace classes.
CLASS_PREVIEW = "preview"
CLASS_DEVELOPMENT = "development"
CLASS_PDI = "pdi"
CLASS_PRODUCTION = "production"
CLASS_TEST = "test"


DEFAULT_INGRESS_CLASS = "traefik"
DB_PORT = 5432
CNPG_DB_NAME = "app"  # default bootstrap db/owner for in-cluster clusters
CNPG_DB_USER = "app"


class NotFound(Exception):
    """A referenced platform object does not exist (yet)."""


@dataclass
class DbConnection:
    """Resolved connection details for an application's database."""

    host: str
    port: int
    name: str
    user: str
    password_secret_namespace: str
    password_secret_name: str
    password_secret_key: str


@dataclass
class DatabaseEndpoint:
    """A managed Database's published coordinates (server + OpenBao credential path).

    The role's password is not a Secret reference yet: the consuming Application
    delivers it into its own namespace from ``openbao_path`` via an ExternalSecret,
    then wires a :class:`DbConnection` pointing at that Secret.
    """

    host: str
    port: int
    name: str
    user: str
    openbao_path: str
    password_key: str


@dataclass
class Effective:
    """The fully-resolved settings used to build an application's resources."""

    client_slug: str
    workspace_name: str
    workspace_class: str
    app_name: str
    namespace: str
    hostname: str
    url: str

    chart_repo_url: str
    chart_name: str
    chart_path: str
    chart_target_revision: str

    ingress_class_name: str
    longpolling: bool

    type_defaults: dict

    # Odoo image resolution (used by the odoo adapter / build pipeline).
    image_repository: str
    image_tag: str

    extra: dict = field(default_factory=dict)


# --- pure helpers ----------------------------------------------------------------


def _slug(spec: dict, name: str) -> str:
    return (spec.get("slug") or name).strip()


def _truncate_label(value: str) -> str:
    return value[:63].rstrip("-")


def namespace_name(client_slug: str, workspace_name: str) -> str:
    """The per-workspace namespace (a single DNS-1123 label)."""
    return _truncate_label(f"{client_slug}-{workspace_name}")


def sanitize_default(workspace_class: str) -> bool:
    """Whether to neutralize a restored DB by default (everything except production)."""
    return workspace_class != CLASS_PRODUCTION


def parse_owner_repo(url: str) -> tuple[str, str]:
    """Parse "owner" and "repo" from a GitHub URL (https or ssh; .git optional)."""
    s = (url or "").strip()
    s = re.sub(r"^git@([^:]+):", r"https://\1/", s)
    s = re.sub(r"^[a-z]+://", "", s)
    s = re.sub(r"\.git$", "", s)
    parts = [p for p in s.split("/") if p]

    if len(parts) >= 3:
        return parts[-2], parts[-1]

    return "", ""


def sanitize_tag(ref: str) -> str:
    """Reduce a git ref to a valid Docker image tag."""
    tag = re.sub(r"[^A-Za-z0-9_.-]", "-", (ref or "").strip())
    tag = tag.strip(".-") or "latest"

    return tag[:128]


def built_image_ref(
    harbor_host: str, project: str, client_slug: str, app_name: str, ref: str
) -> str:
    """The full image reference a build pushes to: host/project/<client>-<app>:<tag>."""
    return f"{harbor_host}/{project}/{client_slug}-{app_name}:{sanitize_tag(ref)}"


def snapshot_object_key(namespace: str, name: str) -> str:
    """The deterministic object-store key for a Snapshot's dump."""
    return f"snapshots/{namespace}/{name}.pgdump"


def cnpg_cluster_name(app_name: str) -> str:
    """The CloudNativePG Cluster name for an application's in-cluster database."""
    return f"{app_name}-db"


def db_credentials_path(prefix: str, server: str, user: str) -> str:
    """The OpenBao KV path holding a Database role's password (pure).

    Keyed by (server, user) so two Databases sharing a user share its password.
    """
    return f"{prefix.strip('/')}/{server}/{user}"


def deep_merge(*layers: dict) -> dict:
    """Recursively merge dicts; later layers win. Lists/scalars are replaced."""
    out: dict = {}

    for layer in layers:
        for k, v in (layer or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v

    return out


def compute(
    cfg: Config,
    *,
    org_spec: dict | None,
    client_name: str,
    client_spec: dict,
    workspace_name: str,
    workspace_spec: dict,
    app_name: str,
    app_spec: dict,
    type_spec: dict,
    domain_fqdn: str = "",
) -> Effective:
    """Fold the layers into the effective settings for an application (pure).

    ``domain_fqdn`` (from a resolved Domain referenced by the app) overrides the
    Organization base domain for hostname generation.
    """
    org = org_spec or {}
    org_domain = org.get("domain") or {}
    org_images = org.get("images") or {}
    org_ingress = org.get("ingress") or {}

    app_ingress = app_spec.get("ingress") or {}
    odoo = app_spec.get("odoo") or {}

    type_chart = type_spec.get("chart") or {}
    type_ingress = type_spec.get("ingress") or {}

    client_slug = _slug(client_spec, client_name)
    workspace_class = workspace_spec.get("class") or CLASS_DEVELOPMENT
    namespace = namespace_name(client_slug, workspace_name)

    base_domain = (domain_fqdn or org_domain.get("base") or cfg.base_domain or "").strip()
    host = (app_ingress.get("host") or "").strip()

    if not host and base_domain:
        # Single DNS label under the base domain so a one-level wildcard
        # (*.base_domain) covers both DNS and the TLS cert. A dotted host like
        # app.workspace.client.base_domain is several levels deep and a wildcard
        # cert/record does not match it (Traefik then serves its default cert and
        # the handshake fails with SSL_ERROR_NO_CYPHER_OVERLAP). Labels are capped
        # at the DNS 63-char limit.
        label = f"{app_name}-{workspace_name}-{client_slug}"[:63].strip("-")
        host = f"{label}.{base_domain}"

    image_repository = org_images.get("odooRepository") or cfg.odoo_image_repository
    image_tag = (odoo.get("version") or "").strip()

    return Effective(
        client_slug=client_slug,
        workspace_name=workspace_name,
        workspace_class=workspace_class,
        app_name=app_name,
        namespace=namespace,
        hostname=host,
        url=f"https://{host}" if host else "",
        chart_repo_url=type_chart.get("repoURL") or "",
        chart_name=type_chart.get("chart") or "",
        chart_path=type_chart.get("path") or "",
        chart_target_revision=type_chart.get("targetRevision") or "",
        ingress_class_name=(
            app_ingress.get("className") or org_ingress.get("className") or DEFAULT_INGRESS_CLASS
        ),
        longpolling=bool(type_ingress.get("longpolling")),
        type_defaults=type_spec.get("defaultValues") or {},
        image_repository=image_repository,
        image_tag=image_tag,
    )


def app_db_connection(app_obj: dict) -> DbConnection:
    """Resolve the connection for an app's first explicit database (spec.databases[0]).

    The database is provisioned by the chart's Database CR on its named DatabaseServer;
    here we read that server's published host and point at the delivered credential
    Secret. Used by snapshot/restore. Raises NotFound until the server publishes a host.
    """
    spec = app_obj.get("spec") or {}
    meta = app_obj.get("metadata") or {}
    status = app_obj.get("status") or {}
    server_ns = meta.get("namespace") or ""
    app_ns = status.get("namespace") or server_ns
    dbs = spec.get("databases") or []

    if not dbs:
        raise NotFound("application has no spec.databases")

    db = dbs[0]
    server_ref = db.get("server")

    if not server_ref:
        raise NotFound("databases[0].server is required")

    server = get_database_server(server_ref, server_ns)
    srv = server.get("status") or {}
    host = srv.get("host")

    if not host:
        raise NotFound(f"DatabaseServer {server_ref!r} not ready (no status.host)")

    creds = db.get("credentials") or {}

    return DbConnection(
        host=host,
        port=int(srv.get("port") or DB_PORT),
        name=db.get("databaseName") or db.get("name"),
        user=db.get("user") or db.get("name"),
        password_secret_namespace=app_ns,
        password_secret_name=creds.get("secret") or "",
        password_secret_key=creds.get("passwordKey") or "password",
    )


# --- cluster fetching ------------------------------------------------------------


def get_client(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_CLIENTS, name, namespace)


def get_workspace(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_WORKSPACES, name, namespace)


def get_application(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_APPLICATIONS, name, namespace)


def get_gitrepository(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_GITREPOSITORIES, name, namespace)


def get_snapshot(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_SNAPSHOTS, name, namespace)


def get_database(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_DATABASES, name, namespace)


def get_database_server(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_DATABASESERVERS, name, namespace)


def get_domain(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_DOMAINS, name, namespace)


def get_sso_application(name: str, namespace: str) -> dict:
    """Fetch a namespaced SSOApplication (identity group), or raise NotFound."""
    api = client.CustomObjectsApi()

    try:
        return api.get_namespaced_custom_object(
            IDENTITY_GROUP, VERSION, namespace, PLURAL_SSOAPPLICATIONS, name
        )
    except ApiException as exc:
        if exc.status == 404:
            raise NotFound(f"SSOApplication {namespace}/{name!r} not found") from exc

        raise


def database_endpoint(db_obj: dict) -> DatabaseEndpoint:
    """Resolve a managed Database's published coordinates from status.connection.

    Raises NotFound until the Database reconciler has provisioned the database and
    role on its server (so the consuming Application requeues).
    """
    name = (db_obj.get("metadata") or {}).get("name") or ""
    status = db_obj.get("status") or {}
    conn = status.get("connection") or {}

    if not conn.get("host") or not conn.get("openbaoPath"):
        raise NotFound(f"Database {name!r} not ready (no status.connection yet)")

    return DatabaseEndpoint(
        host=conn["host"],
        port=int(conn.get("port") or DB_PORT),
        name=conn.get("name") or name,
        user=conn.get("user") or CNPG_DB_USER,
        openbao_path=conn["openbaoPath"],
        password_key=conn.get("passwordKey") or "password",
    )


def get_application_type(name: str) -> dict:
    """Fetch a cluster-scoped ApplicationType by name, or raise NotFound."""
    api = client.CustomObjectsApi()

    try:
        return api.get_cluster_custom_object(GROUP, VERSION, PLURAL_APPLICATIONTYPES, name)
    except ApiException as exc:
        if exc.status == 404:
            raise NotFound(f"ApplicationType {name!r} not found") from exc

        raise


def get_organization(name: str | None) -> dict | None:
    """Resolve the Organization to use (explicit name, else the single one, else None)."""
    api = client.CustomObjectsApi()

    if name:
        try:
            return api.get_cluster_custom_object(GROUP, VERSION, PLURAL_ORGANIZATIONS, name)
        except ApiException as exc:
            if exc.status == 404:
                raise NotFound(f"Organization {name!r} not found") from exc

            raise

    listing = api.list_cluster_custom_object(GROUP, VERSION, PLURAL_ORGANIZATIONS)
    items = listing.get("items") or []

    if len(items) == 1:
        return items[0]

    return None


def _get_namespaced(plural: str, name: str, namespace: str) -> dict:
    api = client.CustomObjectsApi()

    try:
        return api.get_namespaced_custom_object(GROUP, VERSION, namespace, plural, name)
    except ApiException as exc:
        if exc.status == 404:
            raise NotFound(f"{plural[:-1]} {namespace}/{name!r} not found") from exc

        raise
