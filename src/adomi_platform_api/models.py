"""Public request/response schemas (pydantic) — the OpenAPI contract.

Request bodies are the controller objects' ``.spec`` intent (a Client, a Workspace,
an Application, ...); cross-resource refs are taken from the URL path, so the body
only carries the fields a user sets. Reads return a uniform :class:`ResourceStatus`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# --- request bodies (one per controller object) ---------------------------------
class ClientSpec(BaseModel):
    display_name: str = Field(description="Human name of the client.")
    slug: str | None = Field(default=None, description="Stable id; defaults to the resource name.")
    organization: str | None = Field(default=None, description="Owning Organization resource name.")


class DomainSpec(BaseModel):
    fqdn: str = Field(description="The domain, e.g. acme.example.com.")
    wildcard: bool = Field(default=True, description="Issue a wildcard cert (*.fqdn).")
    issuer: str | None = Field(default=None, description="cert-manager ClusterIssuer override.")


class DatabaseSpec(BaseModel):
    engine: str = Field(default="postgres", description="Database engine.")
    storage: str = Field(default="10Gi", description="Persistent volume size.")
    instances: int = Field(default=1, ge=1, description="Replica count (CNPG).")
    environment: str | None = Field(
        default=None, description="Workspace whose namespace hosts the database (environmentRef)."
    )


class WorkspaceSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str | None = None
    workspace_class: str = Field(
        default="development",
        alias="class",
        description="production | development | pdi | preview | test",
    )


class ApplicationSource(BaseModel):
    repository: str = Field(description="GitRepository resource name to build from.")
    ref: str | None = Field(default=None, description="Branch / tag / commit.")


class ApplicationIntegration(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    from_: str = Field(alias="from")


class ApplicationSpec(BaseModel):
    type: str = Field(description="ApplicationType (catalog) resource name.")
    sso: bool = True
    database: str | None = Field(default=None, description="Attach an existing managed Database.")
    database_mode: str | None = Field(
        default=None, description="auto | none | cnpg | external (when not attaching a Database)."
    )
    domain: str | None = Field(default=None, description="Domain resource to host this app under.")
    host: str | None = Field(default=None, description="Explicit hostname override.")
    odoo_version: str | None = None
    source: ApplicationSource | None = None
    integrations: list[ApplicationIntegration] | None = None


class GitRepositoryPreview(BaseModel):
    enabled: bool = True
    client: str | None = Field(default=None, description="Client previews are created for.")
    application_type: str | None = Field(default=None, description="ApplicationType for previews.")


class GitRepositorySpec(BaseModel):
    url: str = Field(description="https/ssh URL of the source repository.")
    default_branch: str = "main"
    credentials_secret: str | None = Field(
        default=None, description="Secret holding a token (key 'token')."
    )
    preview: GitRepositoryPreview | None = None


class SnapshotSpec(BaseModel):
    application: str = Field(description="Application resource name to snapshot.")


# --- responses ------------------------------------------------------------------
class WriteResult(BaseModel):
    """Acknowledgement of a git write (commit/PR)."""

    repo: str
    path: str
    namespace: str | None = None
    committed: bool | None = None
    deleted: bool | None = None
    branch: str | None = None
    reason: str | None = None
    pr: dict | None = None


class ResourceStatus(BaseModel):
    """Live status of a resource, read from its custom resource in the cluster."""

    kind: str
    name: str
    namespace: str
    ready: str | None = None
    message: str | None = None
    phase: str | None = None
    url: str | None = None
    conditions: list[dict] = Field(default_factory=list)
    spec: dict = Field(default_factory=dict)

    @classmethod
    def from_cr(cls, obj: dict) -> "ResourceStatus":
        meta = obj.get("metadata") or {}
        status = obj.get("status") or {}

        ready = message = None
        for cond in status.get("conditions") or []:
            if cond.get("type") == "Ready":
                ready = cond.get("status")
                message = cond.get("message")

        return cls(
            kind=obj.get("kind", ""),
            name=meta.get("name", ""),
            namespace=meta.get("namespace", ""),
            ready=ready,
            message=message,
            phase=status.get("phase"),
            url=status.get("url"),
            conditions=status.get("conditions") or [],
            spec=obj.get("spec") or {},
        )


class Health(BaseModel):
    status: str = "ok"
