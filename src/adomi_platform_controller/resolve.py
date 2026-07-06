"""Resolves the effective configuration for an Application.

An Application's settings come from four layers, each overriding the one before:

    controller Config defaults -> Organization -> ApplicationType -> Application

This module fetches the referenced Organization / Client / Environment / ApplicationType
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
PLURAL_ENVIRONMENTS = "environments"
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

# Environment classes.
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
class Effective:
    """The fully-resolved settings used to build an application's resources."""

    client_slug: str
    environment_name: str
    environment_class: str
    app_name: str
    namespace: str
    hostname: str
    url: str

    chart_repo_url: str
    chart_name: str
    chart_path: str
    chart_target_revision: str

    ingress_class_name: str

    type_defaults: dict

    # Base image for source builds / restore jobs (org images.odooRepository).
    image_repository: str
    image_tag: str

    # The workload's effective env: scope-contributed variables (org -> client ->
    # environment -> application, nearest wins) with the app's explicit spec.env
    # entries overriding everything by name.
    env: list = field(default_factory=list)
    # OpenBao KV paths contributing scoped secrets, least -> most specific (the
    # order IS the precedence: ESO's dataFrom merge lets later keys win).
    scoped_secret_paths: list = field(default_factory=list)

    extra: dict = field(default_factory=dict)


# --- pure helpers ----------------------------------------------------------------


def _slug(spec: dict, name: str) -> str:
    return (spec.get("slug") or name).strip()


def _truncate_label(value: str) -> str:
    return value[:63].rstrip("-")


def namespace_name(client_slug: str, environment_name: str) -> str:
    """The per-environment namespace (a single DNS-1123 label)."""
    return _truncate_label(f"{client_slug}-{environment_name}")


def sanitize_default(environment_class: str) -> bool:
    """Whether to neutralize a restored DB by default (everything except production)."""
    return environment_class != CLASS_PRODUCTION


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


def merged_env(*, org_spec, client_spec, environment_spec, app_spec) -> list[dict]:
    """The workload's effective env from the scope chain (pure).

    Plain ``variables`` declared at each scope merge by name, nearest scope
    winning (org < client < environment < application). The application's
    explicit ``env`` entries (connection wiring, valueFrom refs) override any
    same-named variable and always come through verbatim.
    """
    merged: dict[str, dict] = {}

    for spec in (org_spec or {}, client_spec or {}, environment_spec or {}, app_spec or {}):
        for var in spec.get("variables") or []:
            name = (var.get("name") or "").strip()
            if name:
                merged[name] = {"name": name, "value": var.get("value") or ""}

    for entry in (app_spec or {}).get("env") or []:
        name = (entry.get("name") or "").strip()
        if name:
            merged[name] = entry

    return list(merged.values())


def scoped_secret_paths(
    prefix: str, org_name: str, client_slug: str, environment_name: str, app_name: str
) -> list[str]:
    """OpenBao KV paths contributing scoped secrets, least -> most specific (pure).

    Secret VALUES live only in OpenBao at these paths (one KV map per scope);
    git carries no secret material. The order is the precedence order.
    """
    prefix = prefix.strip("/")
    paths = []
    if org_name:
        paths.append(f"{prefix}/org/{org_name}")
    paths.append(f"{prefix}/clients/{client_slug}")
    paths.append(f"{prefix}/clients/{client_slug}/environments/{environment_name}")
    paths.append(
        f"{prefix}/clients/{client_slug}/environments/{environment_name}/applications/{app_name}"
    )
    return paths


def database_url(user: str, password_key: str, host: str, port: int | str, dbname: str) -> str:
    """The DATABASE_URL Go-template for a delivered credential Secret (pure).

    ESO renders the password in; ``index`` instead of ``.key`` because passwordKey
    may be hyphenated, which is not a valid Go-template field selector.
    """
    return 'postgresql://%s:{{ index . "%s" }}@%s:%s/%s' % (user, password_key, host, port, dbname)


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
    environment_name: str,
    environment_spec: dict,
    app_name: str,
    app_spec: dict,
    type_spec: dict,
    domain_fqdn: str = "",
    org_name: str = "",
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

    type_chart = type_spec.get("chart") or {}

    client_slug = _slug(client_spec, client_name)
    environment_class = environment_spec.get("class") or CLASS_DEVELOPMENT
    namespace = namespace_name(client_slug, environment_name)

    base_domain = (domain_fqdn or org_domain.get("base") or cfg.base_domain or "").strip()
    host = (app_ingress.get("host") or "").strip()

    if not host and base_domain:
        # Single DNS label under the base domain so a one-level wildcard
        # (*.base_domain) covers both DNS and the TLS cert. A dotted host like
        # app.environment.client.base_domain is several levels deep and a wildcard
        # cert/record does not match it (Traefik then serves its default cert and
        # the handshake fails with SSL_ERROR_NO_CYPHER_OVERLAP). Labels are capped
        # at the DNS 63-char limit.
        label = f"{app_name}-{environment_name}-{client_slug}"[:63].strip("-")
        host = f"{label}.{base_domain}"

    image_repository = org_images.get("odooRepository") or cfg.odoo_image_repository
    image_tag = org_images.get("odooTag") or cfg.odoo_image_tag

    return Effective(
        client_slug=client_slug,
        environment_name=environment_name,
        environment_class=environment_class,
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
        type_defaults=type_spec.get("defaultValues") or {},
        image_repository=image_repository,
        image_tag=image_tag,
        env=merged_env(
            org_spec=org,
            client_spec=client_spec,
            environment_spec=environment_spec,
            app_spec=app_spec,
        ),
        scoped_secret_paths=scoped_secret_paths(
            cfg.scoped_secrets_prefix,
            org_name,
            client_slug,
            environment_name,
            app_name,
        ),
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


def get_environment(name: str, namespace: str) -> dict:
    return _get_namespaced(PLURAL_ENVIRONMENTS, name, namespace)


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
            raise NotFound(
                f"referenced {plural}/{name} not found in namespace {namespace}"
            ) from exc

        raise
