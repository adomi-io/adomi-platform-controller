"""Intent → CR ``.spec`` builders, one per controller object.

These map an API request body to the exact ``.spec`` shape the controller's CRDs
reconcile (clientRef, environmentRef, displayName, ...). They are pure functions;
:class:`~.service.ClientService` wraps them with the schema's manifest scaffolding and
commits the result to the Client's client repo.
"""

from __future__ import annotations


def _ref(name: str) -> dict:
    return {"name": name}


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def client_spec(
    *, display_name: str, slug: str | None = None, organization: str | None = None
) -> dict:
    spec = {"displayName": display_name}

    if slug:
        spec["slug"] = slug
    if organization:
        spec["organizationRef"] = _ref(organization)

    return spec


def domain_spec(*, fqdn: str, wildcard: bool = True, issuer: str | None = None) -> dict:
    spec = {"fqdn": fqdn, "wildcard": bool(wildcard)}

    if issuer:
        spec["issuer"] = issuer

    return spec


def databaseserver_spec(
    *,
    engine: str = "postgres",
    mode: str = "cnpg",
    storage: str = "10Gi",
    storage_class: str | None = None,
    instances: int = 1,
    environment: str | None = None,
    host: str | None = None,
    port: int = 5432,
    admin_user: str | None = None,
    admin_openbao_path: str | None = None,
    ssl_mode: str | None = None,
) -> dict:
    spec: dict = {"engine": engine, "mode": mode}

    if mode == "external":
        external = {"host": host, "port": int(port), "sslMode": ssl_mode}
        spec["external"] = _drop_none(external)
    else:  # cnpg
        cnpg: dict = {"storage": storage, "instances": int(instances)}
        if storage_class:
            cnpg["storageClass"] = storage_class
        spec["cnpg"] = cnpg

    admin = _drop_none({"user": admin_user, "openbaoPath": admin_openbao_path})
    if admin:
        spec["admin"] = admin

    if environment:
        spec["environmentRef"] = _ref(environment)

    return spec


def database_spec(
    *,
    server: str,
    database_name: str,
    user: str,
    environment: str | None = None,
) -> dict:
    spec = {"serverRef": _ref(server), "databaseName": database_name, "user": user}

    if environment:
        spec["environmentRef"] = _ref(environment)

    return spec


def environment_spec(
    *, client: str, environment_class: str, display_name: str | None = None
) -> dict:
    return _drop_none(
        {
            "clientRef": _ref(client),
            "class": environment_class,
            "displayName": display_name,
        }
    )


def application_spec(
    *,
    environment: str,
    type: str,
    display_name: str | None = None,
    databases: list[dict] | None = None,
    sso: list[dict] | None = None,
    env: list[dict] | None = None,
    replicas: int | None = None,
    host: str | None = None,
    values: dict | None = None,
    source: dict | None = None,
) -> dict:
    spec: dict = {
        "environmentRef": _ref(environment),
        "type": type,
    }

    if display_name:
        spec["displayName"] = display_name
    if databases:
        spec["databases"] = databases
    if sso:
        spec["sso"] = sso
    if env:
        spec["env"] = env
    if replicas:
        spec["replicas"] = int(replicas)
    if host:
        spec["ingress"] = {"host": host}
    if values:
        spec["values"] = values

    if source and source.get("repository"):
        s = {"repositoryRef": _ref(source["repository"])}
        if source.get("ref"):
            s["ref"] = source["ref"]
        spec["source"] = s

    return spec


def gitrepository_spec(
    *,
    url: str,
    default_branch: str = "main",
    credentials_secret: str | None = None,
    preview: dict | None = None,
) -> dict:
    spec = {"url": url, "defaultBranch": default_branch}

    if credentials_secret:
        spec["credentialsSecretRef"] = _ref(credentials_secret)

    if preview and preview.get("enabled"):
        p = {"enabled": True}
        if preview.get("client"):
            p["clientRef"] = _ref(preview["client"])
        if preview.get("application_type"):
            p["applicationType"] = preview["application_type"]
        spec["preview"] = p

    return spec


def snapshot_spec(*, application: str) -> dict:
    return {"applicationRef": _ref(application)}
