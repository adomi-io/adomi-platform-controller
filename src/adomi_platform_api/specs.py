"""Intent → CR ``.spec`` builders, one per controller object.

These map an API request body to the exact ``.spec`` shape the controller's CRDs
reconcile (clientRef, workspaceRef, displayName, ...). They are pure functions;
:class:`~.service.TenantService` wraps them with the schema's manifest scaffolding and
commits the result to the Client's tenant repo.
"""

from __future__ import annotations


def _ref(name: str) -> dict:
    return {"name": name}


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def client_spec(*, display_name: str, slug: str | None = None, organization: str | None = None) -> dict:
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


def database_spec(
    *, engine: str = "postgres", storage: str = "10Gi", instances: int = 1, environment: str | None = None
) -> dict:
    spec = {"engine": engine, "storage": storage, "instances": int(instances)}

    if environment:
        spec["environmentRef"] = _ref(environment)

    return spec


def workspace_spec(*, client: str, workspace_class: str, display_name: str | None = None) -> dict:
    return _drop_none(
        {
            "clientRef": _ref(client),
            "class": workspace_class,
            "displayName": display_name,
        }
    )


def application_spec(
    *,
    workspace: str,
    type: str,
    sso: bool = True,
    database: str | None = None,
    database_mode: str | None = None,
    domain: str | None = None,
    host: str | None = None,
    odoo_version: str | None = None,
    source: dict | None = None,
    integrations: list[dict] | None = None,
) -> dict:
    spec = {
        "workspaceRef": _ref(workspace),
        "type": type,
        "sso": {"enabled": bool(sso)},
    }

    if database:  # attach an existing managed Database by name
        spec["databaseRef"] = _ref(database)
    elif database_mode and database_mode != "auto":
        spec["database"] = {"mode": database_mode}

    if domain:
        spec["domainRef"] = _ref(domain)
    if host:
        spec["ingress"] = {"host": host}
    if odoo_version:
        spec["odoo"] = {"version": odoo_version}

    if source and source.get("repository"):
        s = {"repositoryRef": _ref(source["repository"])}
        if source.get("ref"):
            s["ref"] = source["ref"]
        spec["source"] = s

    if integrations:
        spec["integrations"] = [
            {"type": i["type"], "fromRef": _ref(i["from"])}
            for i in integrations
            if i.get("type") and i.get("from")
        ]

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
