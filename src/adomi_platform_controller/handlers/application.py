"""ApplicationReconciler — the generic app engine.

An Application runs a catalog app (by ApplicationType) in a Workspace. The reconciler:

  1. resolves Organization -> Client -> Workspace -> ApplicationType into effective config
  2. provisions the database (none | cnpg | external)
  3. (odoo, when spec.source) builds the image via an Argo Workflow, gating deploy
  4. (when spec.restoreFrom) restores+sanitizes a Snapshot into the cnpg DB, gating deploy
  5. declares an SSOApplication (oauth2 or proxy) per the type
  6. runs the app-type adapter + integration connectors to build the Helm values
  7. creates the Argo CD Application that deploys the chart, and publishes a connection
     contract (status.connection) for other apps to integrate with.

Argo CD owns the rendered workload; the controller owns intent + supporting resources.
"""

from __future__ import annotations

import kopf

from .. import (
    argocd,
    buildsecrets,
    cnpg,
    conditions,
    dbjobs,
    github,
    namespaces,
    resolve,
    ssoapps,
    state,
    workflows,
)
from ..apptypes import base
from ..apptypes import registry as apptypes
from ..integrations import registry as integrations
from ._common import fail

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"
PLURAL = "applications"

MANAGED_BY = "adomi-platform-controller"
PUSH_SECRET_NAME = "harbor-push"

BUILD_POLL_DELAY = 15
BUILD_FAIL_DELAY = 120
RESTORE_POLL_DELAY = 15
RESTORE_FAIL_DELAY = 120
INTEGRATION_DELAY = 20

# PR-feedback annotations (set by the preview Sensor).
ANN_REPO = "platform.adomi.io/repo"
ANN_PR_NUMBER = "platform.adomi.io/pr-number"
ANN_COMMIT_SHA = "platform.adomi.io/commit-sha"


def _argo_app_name(eff: resolve.Effective) -> str:
    return f"{eff.namespace}-{eff.app_name}"[:63].rstrip("-")


def _git_secret_name(namespace: str) -> str:
    return f"git-{namespace}"[:253]


def _build_workflow_name(namespace: str, app: str, ref: str) -> str:
    return f"build-{namespace}-{app}-{resolve.sanitize_tag(ref)}"[:253]


def _restore_workflow_name(namespace: str, app: str, snapshot: str) -> str:
    return f"restore-{namespace}-{app}-{snapshot}"[:253]


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def reconcile(spec, meta, status, patch, name, namespace, logger, **_) -> None:
    generation = meta.get("generation", 0)
    cfg = state.provider().config

    eff = _resolve(cfg, spec, name, namespace, patch, status, generation)
    if not eff.hostname:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "no hostname: set spec.ingress.host or provide an Organization base domain",
            generation,
        )

    patch.status["namespace"] = eff.namespace
    patch.status["databaseMode"] = eff.db_mode

    labels = {
        "app.kubernetes.io/managed-by": MANAGED_BY,
        "platform.adomi.io/client": eff.client_slug,
        "platform.adomi.io/workspace": eff.workspace_name,
        "platform.adomi.io/application": eff.app_name,
    }

    # Ensure the workspace namespace exists (the Workspace also does; idempotent,
    # avoids an ordering race for the CNPG/SSO resources created below).
    try:
        namespaces.ensure(eff.namespace, labels)
    except Exception as exc:  # noqa: BLE001
        fail(
            patch, status, conditions.REASON_BACKEND_ERROR, f"ensuring namespace: {exc}", generation
        )

    # Build-from-source (odoo): gate deploy on a successful image build.
    built_image = None
    source = spec.get("source") or None
    if source:
        built_image = _reconcile_build(
            cfg, eff, source, namespace, labels, meta, patch, status, generation, logger
        )

    # Database.
    db_conn = _reconcile_database(cfg, eff, spec, labels, patch, status, generation)

    # Restore-from-snapshot (cnpg): gate deploy on a successful restore.
    if spec.get("restoreFrom"):
        _reconcile_restore(
            cfg, eff, spec, namespace, db_conn, built_image, patch, status, generation, logger
        )

    # SSO.
    sso_slug, sso_secret = ("", "")
    if eff.sso_enabled and eff.sso_protocol:
        sso_slug, sso_secret = _reconcile_sso(eff, spec, patch, status, generation)

    # Resolved image (odoo only; other types use their chart/type-default image).
    image = ""
    if eff.adapter == "odoo":
        image = built_image or (
            f"{eff.image_repository}:{eff.image_tag}" if eff.image_tag else eff.image_repository
        )

    ctx = base.Ctx(
        app_name=eff.app_name,
        namespace=eff.namespace,
        host=eff.hostname,
        url=eff.url,
        ingress_class_name=eff.ingress_class_name,
        longpolling=eff.longpolling,
        list_db=eff.workspace_class != resolve.CLASS_PRODUCTION,
        image=image,
        db=db_conn,
        sso_protocol=eff.sso_protocol if eff.sso_enabled else "",
        sso_secret=sso_secret,
        forward_auth_middleware=cfg.forward_auth_middleware,
        odoo=spec.get("odoo") or {},
        replicas=int(spec.get("replicas") or 1),
        admin_password=spec.get("adminPassword") or None,
        ingress_tls=(spec.get("ingress") or {}).get("tls") or [],
    )

    adapter = apptypes.get(eff.adapter)
    app_values = adapter.helm_values(ctx)
    integration_values = _reconcile_integrations(
        spec.get("integrations") or [], namespace, ctx, patch, status, generation, logger
    )
    values = resolve.deep_merge(
        eff.type_defaults, app_values, integration_values, spec.get("values") or {}
    )

    try:
        argocd.apply(
            argocd.Spec(
                name=_argo_app_name(eff),
                namespace=cfg.argocd_namespace,
                project=cfg.argocd_project,
                repo_url=eff.chart_repo_url,
                path=eff.chart_path,
                chart=eff.chart_name,
                target_revision=eff.chart_target_revision,
                dest_namespace=eff.namespace,
                values=values,
                labels={"app.kubernetes.io/managed-by": MANAGED_BY},
            )
        )
    except Exception as exc:  # noqa: BLE001
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"applying Argo CD Application: {exc}",
            generation,
        )

    patch.status["url"] = eff.url
    patch.status["argoApplication"] = _argo_app_name(eff)
    patch.status["phase"] = "Deployed"
    patch.status["connection"] = adapter.connection(ctx)
    if built_image:
        patch.status["builtImage"] = built_image
    if sso_slug:
        patch.status["ssoSlug"] = sso_slug

    _report_pr(cfg, source, meta, namespace, "success", eff.url, "Preview deployed", True, logger)
    conditions.mark_ready(patch, status, f"Application {eff.app_name!r} reconciled", generation)


def _resolve(cfg, spec, name, namespace, patch, status, generation) -> resolve.Effective:
    workspace_ref = (spec.get("workspaceRef") or {}).get("name")
    type_name = spec.get("type")
    if not workspace_ref:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "workspaceRef.name is required",
            generation,
        )
    if not type_name:
        fail(patch, status, conditions.REASON_INVALID_SPEC, "type is required", generation)

    try:
        workspace = resolve.get_workspace(workspace_ref, namespace)
        ws_spec = workspace.get("spec") or {}
        client_ref = (ws_spec.get("clientRef") or {}).get("name")
        client_obj = resolve.get_client(client_ref, namespace)
        org_ref = ((client_obj.get("spec") or {}).get("organizationRef") or {}).get("name")
        org = resolve.get_organization(org_ref)
        app_type = resolve.get_application_type(type_name)
    except resolve.NotFound as exc:
        fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)

    return resolve.compute(
        cfg,
        org_spec=(org or {}).get("spec") or {},
        client_name=client_ref,
        client_spec=client_obj.get("spec") or {},
        workspace_name=workspace_ref,
        workspace_spec=ws_spec,
        app_name=name,
        app_spec=spec,
        type_spec=app_type.get("spec") or {},
    )


def _reconcile_database(cfg, eff, spec, labels, patch, status, generation):
    if eff.db_mode == resolve.DB_MODE_NONE:
        return None

    if eff.db_mode == resolve.DB_MODE_CNPG:
        cfg_db = (spec.get("database") or {}).get("cnpg") or {}
        cluster = resolve.cnpg_cluster_name(eff.app_name)
        try:
            cnpg.apply(
                cnpg.Spec(
                    name=cluster,
                    namespace=eff.namespace,
                    instances=int(cfg_db.get("instances") or 1),
                    storage_size=cfg_db.get("storage") or "10Gi",
                    storage_class=cfg_db.get("storageClass") or "",
                    database=resolve.CNPG_DB_NAME,
                    owner=resolve.CNPG_DB_USER,
                    labels=labels,
                )
            )
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"provisioning database: {exc}",
                generation,
            )
        return resolve.DbConnection(
            host=f"{cluster}-rw.{eff.namespace}.svc.cluster.local",
            port=resolve.DB_PORT,
            name=resolve.CNPG_DB_NAME,
            user=resolve.CNPG_DB_USER,
            password_secret_namespace=eff.namespace,
            password_secret_name=f"{cluster}-app",
            password_secret_key="password",
        )

    # external
    ext = (spec.get("database") or {}).get("external") or {}
    pw = ext.get("passwordSecret") or {}
    if not ext.get("host") or not pw.get("name"):
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "database.external requires host and passwordSecret.name",
            generation,
        )
    return resolve.DbConnection(
        host=ext["host"],
        port=int(ext.get("port") or resolve.DB_PORT),
        name=ext.get("name") or resolve.CNPG_DB_NAME,
        user=ext.get("user") or resolve.CNPG_DB_USER,
        password_secret_namespace=eff.namespace,
        password_secret_name=pw["name"],
        password_secret_key=pw.get("key") or "db-password",
    )


def _reconcile_build(
    cfg, eff, source, namespace, labels, meta, patch, status, generation, logger
) -> str:
    repo_ref = (source.get("repositoryRef") or {}).get("name")
    if not repo_ref:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "source.repositoryRef.name is required",
            generation,
        )
    try:
        gitrepo = resolve.get_gitrepository(repo_ref, namespace)
    except resolve.NotFound as exc:
        fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)

    repo_spec = gitrepo.get("spec") or {}
    repo_url = repo_spec.get("url")
    if not repo_url:
        fail(
            patch,
            status,
            conditions.REASON_DEPENDENCY_NOT_MET,
            f"GitRepository {repo_ref!r} has no url",
            generation,
        )

    ref = source.get("ref") or repo_spec.get("defaultBranch") or "main"
    harbor_host = cfg.resolved_harbor_host()
    if not harbor_host:
        fail(patch, status, conditions.REASON_INVALID_SPEC, "no Harbor host configured", generation)

    built_image = resolve.built_image_ref(
        harbor_host, cfg.harbor_project, eff.client_slug, eff.app_name, ref
    )
    base_image = source.get("baseImage") or f"{eff.image_repository}:{eff.image_tag or 'latest'}"
    git_secret = _git_secret_name(eff.namespace)

    try:
        bao = state.provider().openbao()
        data = bao.read(cfg.harbor_secret_path) or {}
        password = (data.get(cfg.harbor_secret_key) or "").strip()
        if not password:
            fail(
                patch,
                status,
                conditions.REASON_DEPENDENCY_NOT_MET,
                "Harbor push password missing",
                generation,
            )
        buildsecrets.ensure_dockerconfig_secret(
            PUSH_SECRET_NAME, cfg.argo_namespace, harbor_host, cfg.harbor_username, password
        )
        token = ""
        cred_ref = repo_spec.get("credentialsSecretRef") or {}
        if cred_ref.get("name"):
            token = buildsecrets.read_key(
                cred_ref["name"], namespace, cred_ref.get("key") or "token"
            )
        buildsecrets.ensure_token_secret(git_secret, cfg.argo_namespace, token)
    except kopf.TemporaryError:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"preparing build secrets: {exc}",
            generation,
        )

    wf_name = _build_workflow_name(eff.namespace, eff.app_name, ref)
    try:
        workflows.apply(
            workflows.Spec(
                name=wf_name,
                namespace=cfg.argo_namespace,
                workflow_template_ref=cfg.build_workflow_template,
                service_account=cfg.build_service_account,
                parameters={
                    "repoURL": repo_url,
                    "ref": ref,
                    "contextPath": source.get("contextPath") or ".",
                    "dockerfile": source.get("dockerfile") or "Dockerfile",
                    "baseImage": base_image,
                    "outputImage": built_image,
                    "pushSecret": PUSH_SECRET_NAME,
                    "gitSecret": git_secret,
                },
                labels=labels,
            )
        )
    except Exception as exc:  # noqa: BLE001
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"submitting build Workflow: {exc}",
            generation,
        )

    patch.status["buildWorkflow"] = wf_name
    ph = workflows.phase(workflows.get(wf_name, cfg.argo_namespace))
    if ph == workflows.PHASE_SUCCEEDED:
        return built_image
    if ph in (workflows.PHASE_FAILED, workflows.PHASE_ERROR):
        patch.status["phase"] = "BuildFailed"
        _report_pr(cfg, source, meta, namespace, "failure", eff.url, "Build failed", False, logger)
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"build Workflow {wf_name!r} {ph.lower()}",
            generation,
            delay=BUILD_FAIL_DELAY,
        )
    patch.status["phase"] = "Building"
    _report_pr(cfg, source, meta, namespace, "pending", eff.url, "Building image", False, logger)
    fail(
        patch,
        status,
        conditions.REASON_RECONCILING,
        f"building image (Workflow {wf_name!r})",
        generation,
        delay=BUILD_POLL_DELAY,
    )


def _reconcile_restore(
    cfg, eff, spec, namespace, db_conn, built_image, patch, status, generation, logger
) -> None:
    if eff.db_mode != resolve.DB_MODE_CNPG or db_conn is None:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "restoreFrom requires database.mode 'cnpg'",
            generation,
        )

    restore_from = spec["restoreFrom"]
    snap_ref = (restore_from.get("snapshotRef") or {}).get("name")
    if not snap_ref:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "restoreFrom.snapshotRef.name is required",
            generation,
        )
    if status.get("restoredFrom") == snap_ref:
        return

    try:
        snap = resolve.get_snapshot(snap_ref, namespace)
    except resolve.NotFound as exc:
        fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)
    snap_status = snap.get("status") or {}
    if snap_status.get("phase") != "Completed" or not snap_status.get("location"):
        fail(
            patch,
            status,
            conditions.REASON_DEPENDENCY_NOT_MET,
            f"snapshot {snap_ref!r} not completed",
            generation,
        )

    s3_key = resolve.snapshot_object_key(namespace, snap_ref)
    sanitize = (
        bool(restore_from["sanitize"])
        if "sanitize" in restore_from
        else resolve.sanitize_default(eff.workspace_class)
    )
    odoo_image = built_image or f"{eff.image_repository}:{eff.image_tag or 'latest'}"
    wf_name = _restore_workflow_name(eff.namespace, eff.app_name, snap_ref)

    try:
        db_secret, s3_secret = dbjobs.ensure_secrets(cfg, db_conn)
        workflows.apply(
            workflows.Spec(
                name=wf_name,
                namespace=cfg.argo_namespace,
                workflow_template_ref=cfg.restore_workflow_template,
                service_account=cfg.build_service_account,
                parameters=dbjobs.restore_params(
                    cfg, db_conn, s3_key, db_secret, s3_secret, odoo_image, sanitize
                ),
                labels={"app.kubernetes.io/managed-by": MANAGED_BY},
            )
        )
    except kopf.TemporaryError:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"submitting restore Workflow: {exc}",
            generation,
        )

    patch.status["restoreWorkflow"] = wf_name
    ph = workflows.phase(workflows.get(wf_name, cfg.argo_namespace))
    if ph == workflows.PHASE_SUCCEEDED:
        patch.status["restoredFrom"] = snap_ref
        return
    if ph in (workflows.PHASE_FAILED, workflows.PHASE_ERROR):
        patch.status["phase"] = "RestoreFailed"
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"restore Workflow {wf_name!r} {ph.lower()}",
            generation,
            delay=RESTORE_FAIL_DELAY,
        )
    patch.status["phase"] = "Restoring"
    fail(
        patch,
        status,
        conditions.REASON_RECONCILING,
        f"restoring database (Workflow {wf_name!r})",
        generation,
        delay=RESTORE_POLL_DELAY,
    )


def _reconcile_sso(eff, spec, patch, status, generation) -> tuple[str, str]:
    slug = f"{eff.namespace}-{eff.app_name}"
    display = f"{eff.client_slug} / {eff.workspace_name} / {eff.app_name}"
    try:
        if eff.sso_protocol == "oauth2":
            target_secret = f"{eff.app_name}-oidc"
            paths = eff.sso_redirect_paths or ["/oauth-authorized/authentik"]
            ssoapps.apply_oauth2(
                ssoapps.OAuth2Spec(
                    name=eff.app_name,
                    namespace=eff.namespace,
                    slug=slug,
                    display_name=display,
                    redirect_uris=[f"{eff.url}{p}" for p in paths],
                    target_secret=target_secret,
                    group=eff.client_slug,
                )
            )
            return slug, target_secret
        ssoapps.apply(
            ssoapps.ProxySpec(
                name=eff.app_name,
                namespace=eff.namespace,
                slug=slug,
                display_name=display,
                external_host=eff.url,
                group=eff.client_slug,
            )
        )
        return slug, ""
    except Exception as exc:  # noqa: BLE001
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"declaring SSOApplication: {exc}",
            generation,
        )


def _reconcile_integrations(items, namespace, ctx, patch, status, generation, logger) -> dict:
    merged: dict = {}
    for item in items:
        itype = item.get("type")
        ref = (item.get("fromRef") or {}).get("name")
        connector = integrations.get(itype)
        if connector is None:
            logger.info(f"unknown integration type {itype!r}; skipping")
            continue
        try:
            provider = resolve.get_application(ref, namespace)
        except resolve.NotFound as exc:
            fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)
        pstatus = provider.get("status") or {}
        connection = pstatus.get("connection")
        if not _ready(pstatus) or not connection:
            fail(
                patch,
                status,
                conditions.REASON_DEPENDENCY_NOT_MET,
                f"integration provider {ref!r} not ready",
                generation,
                delay=INTEGRATION_DELAY,
            )
        merged = resolve.deep_merge(merged, connector.values(connection, ctx))
    return merged


def _ready(obj_status: dict) -> bool:
    for cond in (obj_status or {}).get("conditions") or []:
        if cond.get("type") == "Ready":
            return cond.get("status") == "True"
    return False


# --- PR feedback (preview applications) ------------------------------------------


def _pr_info(meta):
    ann = meta.get("annotations") or {}
    repo = ann.get(ANN_REPO) or ""
    number = ann.get(ANN_PR_NUMBER) or ""
    sha = ann.get(ANN_COMMIT_SHA) or ""
    if "/" not in repo or not number or not sha:
        return None
    owner, _, name = repo.partition("/")
    try:
        return owner, name, int(number), sha
    except ValueError:
        return None


def _github_client(cfg, source, namespace):
    repo_ref = (source or {}).get("repositoryRef", {}).get("name") if source else None
    if not repo_ref:
        return None
    try:
        gitrepo = resolve.get_gitrepository(repo_ref, namespace)
    except resolve.NotFound:
        return None
    cred = (gitrepo.get("spec") or {}).get("credentialsSecretRef") or {}
    if not cred.get("name"):
        return None
    token = buildsecrets.read_key(cred["name"], namespace, cred.get("key") or "token")
    return github.GitHubClient(token, cfg.github_api_url)


def _report_pr(
    cfg, source, meta, namespace, state_, target_url, description, comment, logger
) -> None:
    info = _pr_info(meta)
    if not info:
        return
    owner, repo, number, sha = info
    try:
        gh = _github_client(cfg, source, namespace)
        if gh is None:
            return
        gh.set_commit_status(owner, repo, sha, state_, target_url, description)
        if comment and target_url:
            gh.upsert_pr_comment(owner, repo, number, github.preview_comment_body(target_url))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"PR feedback failed: {exc}")


@kopf.on.delete(GROUP, VERSION, PLURAL)
def finalize(spec, status, name, namespace, logger, **_) -> None:
    """Best-effort teardown of the app's own resources (not the shared workspace ns)."""
    cfg = state.provider().config
    ns = status.get("namespace")
    app_name = name

    app_ref = status.get("argoApplication")
    if app_ref:
        try:
            argocd.delete(app_ref, cfg.argocd_namespace)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed deleting Argo CD Application {app_ref!r}: {exc}")
    if ns:
        try:
            cnpg.delete(resolve.cnpg_cluster_name(app_name), ns)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed deleting CNPG cluster during finalize: {exc}")
        try:
            ssoapps.delete(app_name, ns)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed deleting SSOApplication during finalize: {exc}")
    for wf in (status.get("buildWorkflow"), status.get("restoreWorkflow")):
        if wf:
            try:
                workflows.delete(wf, cfg.argo_namespace)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed deleting Workflow {wf!r} during finalize: {exc}")
