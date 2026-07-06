"""ApplicationReconciler — the generic app engine.

An Application runs a catalog app (by ApplicationType) in an Environment. The reconciler:

  1. resolves Organization -> Client -> Environment -> ApplicationType into effective config
  2. (when spec.source) builds the image via an Argo Workflow, gating deploy
  3. (when spec.restoreFrom) restores+sanitizes a Snapshot into the app's database
  4. maps the explicit intent (databases / sso / env / ingress) onto the chart's value
     contract (chartvalues.build_chart_values) and creates the Argo CD Application

The chart owns value-shaping and emits its own capability CRs (Database / SSOApplication);
the controller provisions nothing inline. Argo CD owns the rendered workload.
"""

from __future__ import annotations

import kopf

from .. import (
    argocd,
    buildsecrets,
    chartvalues,
    conditions,
    dbjobs,
    externalsecrets,
    github,
    namespaces,
    oidc,
    resolve,
    state,
    workflows,
)
from ._common import Reconciler, fail

PUSH_SECRET_NAME = "harbor-push"

BUILD_POLL_DELAY = 15
BUILD_FAIL_DELAY = 120
RESTORE_POLL_DELAY = 15
RESTORE_FAIL_DELAY = 120
INTEGRATION_DELAY = 20
SSO_POLL_DELAY = 15

# PR-feedback annotations (set by the preview Sensor).
ANN_REPO = "platform.adomi.io/repo"
ANN_PR_NUMBER = "platform.adomi.io/pr-number"
ANN_COMMIT_SHA = "platform.adomi.io/commit-sha"


class ApplicationReconciler(Reconciler):
    plural = "applications"

    @staticmethod
    def _argo_app_name(eff: resolve.Effective) -> str:
        return f"{eff.namespace}-{eff.app_name}"[:63].rstrip("-")

    @staticmethod
    def _resolve_oidc(cfg, spec, namespace: str) -> tuple[dict, bool]:
        """Resolve the primary SSOApplication's OIDC descriptor for value injection.

        Returns (descriptor, ready). ready is True (nothing to wait on) unless an
        oauth2 SSO entry exists whose SSOApplication has not yet published a client-id —
        then ready is False so the caller requeues. The descriptor is empty until ready
        so the chart renders without OIDC values on the first pass (which creates the
        SSOApplication), then with them once Authentik has minted the client.
        """
        sso_entries = spec.get("sso") or []
        primary = sso_entries[0] if sso_entries else None

        if not primary or (primary.get("protocol") or "oauth2") != "oauth2":
            return {}, True

        secret = (primary.get("credentials") or {}).get("secret")
        authority = cfg.resolved_authentik_url()

        # Without a delivered Secret or a configured public Authentik URL there is
        # nothing to inject and nothing to gate on.
        if not secret or not authority:
            return {}, True

        try:
            sso = resolve.get_sso_application(primary["name"], namespace)
        except resolve.NotFound:
            return {}, False  # chart hasn't created it yet — requeue

        client_id = (sso.get("status") or {}).get("clientID")

        if not client_id:
            return {}, False  # created but not reconciled — requeue

        creds = primary.get("credentials") or {}
        descriptor = oidc.descriptor_values(
            authority,
            (sso.get("status") or {}).get("slug") or primary.get("slug") or primary["name"],
            client_id=client_id,
            secret=secret,
            scopes=primary.get("scopes"),
            client_secret_key=creds.get("clientSecretKey") or "client-secret",
        )

        return descriptor, True

    @staticmethod
    def _git_secret_name(namespace: str) -> str:
        return f"git-{namespace}"[:253]

    @staticmethod
    def _github_app_token(cfg, bao, repo_url: str, logger) -> str:
        """A fresh App installation token for a github.com repo, or "".

        Empty means App auth does not apply (non-GitHub host, App not
        configured, App not installed on the repo) — public repositories still
        build fine without credentials; a private one surfaces the clone
        failure on its build Workflow.
        """
        if not cfg.github_app_secret_path or not github.is_github_url(repo_url):
            return ""

        creds = bao.read(cfg.github_app_secret_path) or {}
        app_id = str(creds.get(cfg.github_app_id_key) or "").strip()
        private_key = creds.get(cfg.github_app_private_key_key) or ""

        if not app_id or not private_key:
            return ""

        owner, repo = resolve.parse_owner_repo(repo_url)

        if not owner or not repo:
            return ""

        try:
            auth = github.app_auth(app_id, private_key, cfg.github_api_url)

            return auth.installation_token_for(owner, repo)
        except github.GitHubAppError as exc:
            logger.warning(f"building {owner}/{repo} without credentials: {exc}")

            return ""

    @staticmethod
    def _build_workflow_name(namespace: str, app: str, ref: str) -> str:
        return f"build-{namespace}-{app}-{resolve.sanitize_tag(ref)}"[:253]

    @staticmethod
    def _restore_workflow_name(namespace: str, app: str, snapshot: str) -> str:
        return f"restore-{namespace}-{app}-{snapshot}"[:253]

    def reconcile(self, spec, meta, status, patch, name, namespace, logger, **_) -> None:
        generation = meta.get("generation", 0)
        cfg = state.provider().config

        eff = self._resolve(cfg, spec, name, namespace, patch, status, generation)

        if not eff.hostname:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "no hostname: set spec.ingress.host or provide an Organization base domain",
                generation,
            )

        patch.status["namespace"] = eff.namespace

        labels = {
            "app.kubernetes.io/managed-by": self.MANAGED_BY,
            "platform.adomi.io/client": eff.client_slug,
            "platform.adomi.io/environment": eff.environment_name,
            "platform.adomi.io/application": eff.app_name,
        }

        # Ensure the environment namespace exists (the Environment also does; idempotent,
        # avoids an ordering race for the CNPG/SSO resources created below).
        try:
            namespaces.Namespace(eff.namespace, labels).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"ensuring namespace: {exc}",
                generation,
            )

        # Build-from-source (odoo): gate deploy on a successful image build.
        built_image = None
        source = spec.get("source") or None

        if source:
            built_image = self._reconcile_build(
                cfg,
                eff,
                source,
                namespace,
                labels,
                meta,
                patch,
                status,
                generation,
                logger,
            )

        # Restore-from-snapshot (odoo): gate deploy on a successful restore, into the
        # app's first explicit database (the chart provisions it via its Database CR).
        if spec.get("restoreFrom"):
            self._reconcile_restore(
                cfg, eff, spec, namespace, built_image, patch, status, generation, logger
            )

        # The chart owns value-shaping and emits its own capability CRs (Database /
        # SSOApplication) from the explicit databases/sso lists. The controller only maps
        # the intent onto the chart's value contract — nothing inferred, nothing
        # provisioned inline.
        image = built_image or ""
        # Resolve the primary SSOApplication's OIDC descriptor (client-id comes from its
        # published status). Injected as .Values.oidc so charts can wire runtime SSO
        # config from values; sso_ready is False until Authentik has minted the client.
        oidc_values, sso_ready = self._resolve_oidc(cfg, spec, eff.namespace)
        env_from_secret = self._reconcile_scoped_secrets(eff, labels, logger)
        values = resolve.deep_merge(
            eff.type_defaults,
            chartvalues.build_chart_values(
                client_slug=eff.client_slug,
                replicas=int(spec.get("replicas") or 1),
                image=image,
                ingress_host=eff.hostname,
                ingress_class_name=eff.ingress_class_name,
                ingress_tls=(spec.get("ingress") or {}).get("tls") or [],
                ingress_annotations=(spec.get("ingress") or {}).get("annotations") or None,
                databases=spec.get("databases") or [],
                sso=spec.get("sso") or [],
                env=eff.env,
                oidc=oidc_values,
                env_from_secret=env_from_secret,
            ),
            spec.get("values") or {},
        )

        try:
            argocd.ArgoApplication(
                name=self._argo_app_name(eff),
                namespace=cfg.argocd_namespace,
                project=cfg.argocd_project,
                repo_url=eff.chart_repo_url,
                path=eff.chart_path,
                chart=eff.chart_name,
                target_revision=eff.chart_target_revision,
                dest_namespace=eff.namespace,
                values=values,
                labels={"app.kubernetes.io/managed-by": self.MANAGED_BY},
            ).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"applying Argo CD Application: {exc}",
                generation,
            )

        patch.status["url"] = eff.url
        patch.status["argoApplication"] = self._argo_app_name(eff)
        patch.status["phase"] = "Deployed"

        if built_image:
            patch.status["builtImage"] = built_image

        self._report_pr(
            cfg, source, meta, namespace, "success", eff.url, "Preview deployed", True, logger
        )

        # The Argo Application (and thus the chart-emitted SSOApplication) is applied; if
        # SSO isn't fully reconciled yet, requeue so the next pass injects the descriptor
        # (client-id) into the chart values and wires the app's runtime SSO config.
        if not sso_ready:
            fail(
                patch,
                status,
                conditions.REASON_RECONCILING,
                "waiting for SSO client credentials to wire the application",
                generation,
                delay=SSO_POLL_DELAY,
            )

        conditions.mark_ready(patch, status, f"Application {eff.app_name!r} reconciled", generation)

    def _scoped_secret_name(self, app_name: str) -> str:
        return f"{app_name}-scoped-secrets"[:253]

    def _reconcile_scoped_secrets(self, eff, labels, logger) -> str:
        """Deliver scoped Secrets (org/client/environment/app) into the workload.

        One ExternalSecret pulls every key from each scope's OpenBao path that
        exists, in least->most specific order (ESO's merge makes later keys win).
        Returns the delivered Secret name for the chart's ``envFrom``, or "" when
        no scope holds any secrets (the ExternalSecret is removed then, so a
        deleted last secret revokes cleanly). Best-effort: a delivery failure
        must not block the deploy of an app that doesn't use scoped secrets.
        """
        cfg = state.provider().config
        name = self._scoped_secret_name(eff.app_name)

        try:
            bao = state.provider().openbao()
            paths = [p for p in eff.scoped_secret_paths if bao.read(p)]

            if not paths:
                externalsecrets.ExternalSecret.delete(name, eff.namespace)
                return ""

            externalsecrets.ExternalSecret(
                name=name,
                namespace=eff.namespace,
                store_name=cfg.cluster_secret_store,
                remote_path="",
                data_from_paths=paths,
                refresh_interval="1m",
                labels=labels,
            ).apply()

            return name
        except Exception as exc:  # noqa: BLE001 - scoped secrets are additive
            logger.warning("scoped secrets for %s not delivered: %s", eff.app_name, exc)
            return ""

    def _resolve(self, cfg, spec, name, namespace, patch, status, generation) -> resolve.Effective:
        environment_ref = (spec.get("environmentRef") or {}).get("name")
        type_name = spec.get("type")

        if not environment_ref:
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "environmentRef.name is required",
                generation,
            )

        if not type_name:
            fail(patch, status, conditions.REASON_INVALID_SPEC, "type is required", generation)

        domain_fqdn = ""
        domain_ref = (spec.get("domainRef") or {}).get("name")

        try:
            environment = resolve.get_environment(environment_ref, namespace)
            ws_spec = environment.get("spec") or {}
            client_ref = (ws_spec.get("clientRef") or {}).get("name")
            client_obj = resolve.get_client(client_ref, namespace)
            org_ref = ((client_obj.get("spec") or {}).get("organizationRef") or {}).get("name")
            org = resolve.get_organization(org_ref)
            app_type = resolve.get_application_type(type_name)

            if domain_ref:
                domain_obj = resolve.get_domain(domain_ref, namespace)
                domain_fqdn = (
                    (domain_obj.get("status") or {}).get("host")
                    or (domain_obj.get("spec") or {}).get("fqdn")
                    or ""
                )
        except resolve.NotFound as exc:
            fail(patch, status, conditions.REASON_DEPENDENCY_NOT_MET, str(exc), generation)

        return resolve.compute(
            cfg,
            org_spec=(org or {}).get("spec") or {},
            client_name=client_ref,
            client_spec=client_obj.get("spec") or {},
            environment_name=environment_ref,
            environment_spec=ws_spec,
            app_name=name,
            app_spec=spec,
            type_spec=app_type.get("spec") or {},
            domain_fqdn=domain_fqdn,
            org_name=((org or {}).get("metadata") or {}).get("name") or "",
        )

    def _reconcile_build(
        self, cfg, eff, source, namespace, labels, meta, patch, status, generation, logger
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
            fail(
                patch,
                status,
                conditions.REASON_INVALID_SPEC,
                "no Harbor host configured",
                generation,
            )

        built_image = resolve.built_image_ref(
            harbor_host,
            cfg.harbor_project,
            eff.client_slug,
            eff.app_name,
            ref,
        )
        base_image = source.get("baseImage") or f"{eff.image_repository}:latest"
        git_secret = self._git_secret_name(eff.namespace)

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

            buildsecrets.ManagedSecret.dockerconfig(
                PUSH_SECRET_NAME,
                cfg.argo_namespace,
                harbor_host,
                cfg.harbor_username,
                password,
            ).apply()

            cred_ref = repo_spec.get("credentialsSecretRef") or {}

            if cred_ref.get("name"):
                token = buildsecrets.ManagedSecret.read_key(
                    cred_ref["name"],
                    namespace,
                    cred_ref.get("key") or "token",
                )
            else:
                token = self._github_app_token(cfg, bao, repo_url, logger)

            buildsecrets.ManagedSecret.token(git_secret, cfg.argo_namespace, token).apply()
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

        wf_name = self._build_workflow_name(eff.namespace, eff.app_name, ref)

        try:
            workflows.Workflow(
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
            ).apply()
        except Exception as exc:  # noqa: BLE001
            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"submitting build Workflow: {exc}",
                generation,
            )

        patch.status["buildWorkflow"] = wf_name

        ph = workflows.phase(workflows.Workflow.read(wf_name, cfg.argo_namespace))

        if ph == workflows.PHASE_SUCCEEDED:
            return built_image

        if ph in (workflows.PHASE_FAILED, workflows.PHASE_ERROR):
            patch.status["phase"] = "BuildFailed"

            self._report_pr(
                cfg, source, meta, namespace, "failure", eff.url, "Build failed", False, logger
            )

            fail(
                patch,
                status,
                conditions.REASON_BACKEND_ERROR,
                f"build Workflow {wf_name!r} {ph.lower()}",
                generation,
                delay=BUILD_FAIL_DELAY,
            )

        patch.status["phase"] = "Building"

        self._report_pr(
            cfg, source, meta, namespace, "pending", eff.url, "Building image", False, logger
        )

        fail(
            patch,
            status,
            conditions.REASON_RECONCILING,
            f"building image (Workflow {wf_name!r})",
            generation,
            delay=BUILD_POLL_DELAY,
        )

    def _reconcile_restore(
        self, cfg, eff, spec, namespace, built_image, patch, status, generation, logger
    ) -> None:
        # Resolve the target DB from the app's first explicit database (provisioned by
        # the chart's Database CR on its named DatabaseServer).
        try:
            db_conn = resolve.app_db_connection(
                {
                    "spec": spec,
                    "metadata": {"namespace": namespace},
                    "status": {"namespace": eff.namespace},
                }
            )
        except resolve.NotFound as exc:
            fail(
                patch,
                status,
                conditions.REASON_DEPENDENCY_NOT_MET,
                f"restoreFrom: {exc}",
                generation,
                delay=INTEGRATION_DELAY,
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
            else resolve.sanitize_default(eff.environment_class)
        )
        odoo_image = built_image or f"{eff.image_repository}:latest"
        wf_name = self._restore_workflow_name(eff.namespace, eff.app_name, snap_ref)

        try:
            db_secret, s3_secret = dbjobs.ensure_secrets(cfg, db_conn)

            workflows.Workflow(
                name=wf_name,
                namespace=cfg.argo_namespace,
                workflow_template_ref=cfg.restore_workflow_template,
                service_account=cfg.build_service_account,
                parameters=dbjobs.restore_params(
                    cfg,
                    db_conn,
                    s3_key,
                    db_secret,
                    s3_secret,
                    odoo_image,
                    sanitize,
                ),
                labels={"app.kubernetes.io/managed-by": self.MANAGED_BY},
            ).apply()
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

        ph = workflows.phase(workflows.Workflow.read(wf_name, cfg.argo_namespace))

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

    # --- PR feedback (preview applications) ------------------------------------------

    def _pr_info(self, meta):
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

    def _github_client(self, cfg, source, namespace):
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

        token = buildsecrets.ManagedSecret.read_key(
            cred["name"], namespace, cred.get("key") or "token"
        )

        return github.GitHubClient(token, cfg.github_api_url)

    def _report_pr(
        self, cfg, source, meta, namespace, state_, target_url, description, comment, logger
    ) -> None:
        info = self._pr_info(meta)

        if not info:
            return

        owner, repo, number, sha = info

        try:
            gh = self._github_client(cfg, source, namespace)

            if gh is None:
                return

            gh.set_commit_status(owner, repo, sha, state_, target_url, description)

            if comment and target_url:
                gh.upsert_pr_comment(owner, repo, number, github.preview_comment_body(target_url))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"PR feedback failed: {exc}")

    def finalize(self, spec, status, name, namespace, logger, **_) -> None:
        """Best-effort teardown of the app's own resources (not the shared environment ns)."""
        cfg = state.provider().config

        app_ref = status.get("argoApplication")

        if app_ref:
            try:
                argocd.ArgoApplication.delete(app_ref, cfg.argocd_namespace)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed deleting Argo CD Application {app_ref!r}: {exc}")

        for wf in (status.get("buildWorkflow"), status.get("restoreWorkflow")):
            if wf:
                try:
                    workflows.Workflow.delete(wf, cfg.argo_namespace)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Failed deleting Workflow {wf!r} during finalize: {exc}")


_reconciler = ApplicationReconciler()


@kopf.on.create(
    ApplicationReconciler.GROUP, ApplicationReconciler.VERSION, ApplicationReconciler.plural
)
@kopf.on.update(
    ApplicationReconciler.GROUP, ApplicationReconciler.VERSION, ApplicationReconciler.plural
)
@kopf.on.resume(
    ApplicationReconciler.GROUP, ApplicationReconciler.VERSION, ApplicationReconciler.plural
)
def reconcile(**kwargs) -> None:
    return _reconciler.reconcile(**kwargs)


@kopf.on.delete(
    ApplicationReconciler.GROUP, ApplicationReconciler.VERSION, ApplicationReconciler.plural
)
def finalize(**kwargs) -> None:
    return _reconciler.finalize(**kwargs)
