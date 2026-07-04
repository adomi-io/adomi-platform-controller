"""Scoped Variables + Secrets (the GitHub Actions model, four scopes).

Variables are plain values committed onto the scope's CR in the client repo
(``spec.variables``) — git stays the source of truth and the controller merges
the chain (organization < client < environment < application) into each
workload's env. Secret VALUES never touch git: they go straight to OpenBao at
the matching scope path and the controller delivers the merged map via an
ExternalSecret. GET on secrets returns names only, by design.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from adomi_platform_schema import SchemaError, validate_name

from ..config import Settings, get_settings
from ..deps import get_secrets_store, get_service
from ..models import ScopedValue, VariableEntry, WriteResult
from ..secrets import ScopedSecretsStore, SecretsError
from ..service import ClientService, NotFoundError

router = APIRouter(tags=["scoped-config"])


def _scope(client: str, environment: str = "", application: str = "") -> tuple[str, str]:
    """(plural, name) of the CR a scope's variables live on."""
    if application:
        return "applications", application
    if environment:
        return "environments", environment
    return "clients", client


def _secret_path(
    settings: Settings, client: str, environment: str = "", application: str = ""
) -> str:
    prefix = settings.scoped_secrets_prefix.strip("/")
    path = f"{prefix}/clients/{client}"
    if environment:
        path += f"/environments/{environment}"
    if application:
        path += f"/applications/{application}"
    return path


def _org_secret_path(settings: Settings, org: str) -> str:
    return f"{settings.scoped_secrets_prefix.strip('/')}/org/{org}"


def _put_variable(service, client, plural, name, var, body) -> WriteResult:
    try:
        return WriteResult(**service.set_variable(client, plural, name, var, body.value))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SchemaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _delete_variable(service, client, plural, name, var) -> WriteResult:
    try:
        return WriteResult(**service.remove_variable(client, plural, name, var))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SchemaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _list_variables(service, client, plural, name) -> list[VariableEntry]:
    try:
        return [VariableEntry(**v) for v in service.variables(client, plural, name)]
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def _put_secret(store, path, name, body) -> dict:
    _validate_secret_name(name)
    try:
        store.set(path, name, body.value)
    except SecretsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"stored": True, "name": name}


def _delete_secret(store, path, name) -> dict:
    try:
        removed = store.remove(path, name)
    except SecretsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"deleted": removed, "name": name}


def _list_secrets(store, path) -> list[str]:
    try:
        return store.names(path)
    except SecretsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


def _validate_secret_name(name: str) -> None:
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "secret names are alphanumeric with '-' / '_'",
        )


# --- client scope -----------------------------------------------------------------
@router.get("/clients/{client}/variables", response_model=list[VariableEntry])
def list_client_variables(client: str, service: ClientService = Depends(get_service)):
    return _list_variables(service, client, *_scope(client))


@router.put("/clients/{client}/variables/{var}", response_model=WriteResult)
def put_client_variable(
    client: str, var: str, body: ScopedValue, service: ClientService = Depends(get_service)
):
    return _put_variable(service, client, *_scope(client), var, body)


@router.delete("/clients/{client}/variables/{var}", response_model=WriteResult)
def delete_client_variable(client: str, var: str, service: ClientService = Depends(get_service)):
    return _delete_variable(service, client, *_scope(client), var)


@router.get("/clients/{client}/secrets", response_model=list[str])
def list_client_secrets(
    client: str,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    return _list_secrets(store, _secret_path(settings, client))


@router.put("/clients/{client}/secrets/{name}")
def put_client_secret(
    client: str,
    name: str,
    body: ScopedValue,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    return _put_secret(store, _secret_path(settings, client), name, body)


@router.delete("/clients/{client}/secrets/{name}")
def delete_client_secret(
    client: str,
    name: str,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    return _delete_secret(store, _secret_path(settings, client), name)


# --- environment scope --------------------------------------------------------------
@router.get(
    "/clients/{client}/environments/{environment}/variables",
    response_model=list[VariableEntry],
)
def list_environment_variables(
    client: str, environment: str, service: ClientService = Depends(get_service)
):
    return _list_variables(service, client, *_scope(client, environment))


@router.put(
    "/clients/{client}/environments/{environment}/variables/{var}",
    response_model=WriteResult,
)
def put_environment_variable(
    client: str,
    environment: str,
    var: str,
    body: ScopedValue,
    service: ClientService = Depends(get_service),
):
    return _put_variable(service, client, *_scope(client, environment), var, body)


@router.delete(
    "/clients/{client}/environments/{environment}/variables/{var}",
    response_model=WriteResult,
)
def delete_environment_variable(
    client: str, environment: str, var: str, service: ClientService = Depends(get_service)
):
    return _delete_variable(service, client, *_scope(client, environment), var)


@router.get("/clients/{client}/environments/{environment}/secrets", response_model=list[str])
def list_environment_secrets(
    client: str,
    environment: str,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    validate_name(environment, "environment")
    return _list_secrets(store, _secret_path(settings, client, environment))


@router.put("/clients/{client}/environments/{environment}/secrets/{name}")
def put_environment_secret(
    client: str,
    environment: str,
    name: str,
    body: ScopedValue,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    validate_name(environment, "environment")
    return _put_secret(store, _secret_path(settings, client, environment), name, body)


@router.delete("/clients/{client}/environments/{environment}/secrets/{name}")
def delete_environment_secret(
    client: str,
    environment: str,
    name: str,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    validate_name(environment, "environment")
    return _delete_secret(store, _secret_path(settings, client, environment), name)


# --- application scope --------------------------------------------------------------
@router.get(
    "/clients/{client}/environments/{environment}/applications/{application}/variables",
    response_model=list[VariableEntry],
)
def list_application_variables(
    client: str,
    environment: str,
    application: str,
    service: ClientService = Depends(get_service),
):
    return _list_variables(service, client, *_scope(client, environment, application))


@router.put(
    "/clients/{client}/environments/{environment}/applications/{application}/variables/{var}",
    response_model=WriteResult,
)
def put_application_variable(
    client: str,
    environment: str,
    application: str,
    var: str,
    body: ScopedValue,
    service: ClientService = Depends(get_service),
):
    return _put_variable(service, client, *_scope(client, environment, application), var, body)


@router.delete(
    "/clients/{client}/environments/{environment}/applications/{application}/variables/{var}",
    response_model=WriteResult,
)
def delete_application_variable(
    client: str,
    environment: str,
    application: str,
    var: str,
    service: ClientService = Depends(get_service),
):
    return _delete_variable(service, client, *_scope(client, environment, application), var)


@router.get(
    "/clients/{client}/environments/{environment}/applications/{application}/secrets",
    response_model=list[str],
)
def list_application_secrets(
    client: str,
    environment: str,
    application: str,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    validate_name(environment, "environment")
    validate_name(application, "application")
    return _list_secrets(store, _secret_path(settings, client, environment, application))


@router.put(
    "/clients/{client}/environments/{environment}/applications/{application}/secrets/{name}"
)
def put_application_secret(
    client: str,
    environment: str,
    application: str,
    name: str,
    body: ScopedValue,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    validate_name(environment, "environment")
    validate_name(application, "application")
    return _put_secret(store, _secret_path(settings, client, environment, application), name, body)


@router.delete(
    "/clients/{client}/environments/{environment}/applications/{application}/secrets/{name}"
)
def delete_application_secret(
    client: str,
    environment: str,
    application: str,
    name: str,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(client, "customer")
    validate_name(environment, "environment")
    validate_name(application, "application")
    return _delete_secret(store, _secret_path(settings, client, environment, application), name)


# --- organization scope (secrets only: the Organization CR is platform-side) ---------
@router.get("/organizations/{org}/secrets", response_model=list[str])
def list_org_secrets(
    org: str,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(org, "organization")
    return _list_secrets(store, _org_secret_path(settings, org))


@router.put("/organizations/{org}/secrets/{name}")
def put_org_secret(
    org: str,
    name: str,
    body: ScopedValue,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(org, "organization")
    return _put_secret(store, _org_secret_path(settings, org), name, body)


@router.delete("/organizations/{org}/secrets/{name}")
def delete_org_secret(
    org: str,
    name: str,
    store: ScopedSecretsStore = Depends(get_secrets_store),
    settings: Settings = Depends(get_settings),
):
    validate_name(org, "organization")
    return _delete_secret(store, _org_secret_path(settings, org), name)
