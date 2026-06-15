# adomi-platform-controller agent guide

A [Kopf](https://kopf.readthedocs.io/)-based Python Kubernetes operator. It
reconciles two CRD families:

- `SSOApplication` (`identity.adomi.io`) into Authentik, OpenBao, and External
  Secrets, with OpenBao as the source of truth for credentials.
- The application platform (`platform.adomi.io`): `Organization` → `Client` →
  `Workspace` → `Application`, plus `ApplicationType` (the cluster-scoped catalog),
  `GitRepository` (a build input) and `Snapshot` (a DB dump to clone/restore).
  An `Application` runs any catalog app (by `type`): the engine provisions a
  namespace (the Workspace's), a database (`none`/CloudNativePG/external), an
  `SSOApplication` (oauth2 or proxy), runs the type's **adapter** + integration
  connectors to build the chart values, and creates an **Argo CD `Application`**
  that deploys the chart. Odoo additionally builds-from-source (Argo Workflow →
  Harbor) and restores/sanitizes Snapshots. The controller owns intent + supporting
  resources; Argo CD owns the rendered workload.

## Project structure

```
src/adomi_platform_controller/
  __main__.py          `python -m adomi_platform_controller` entry (wraps kopf.run)
  operator.py          @kopf.on.startup hook + imports handlers to register them
  config.py            Config dataclass; Config.from_env() reads env vars
  state.py             process-wide Provider singleton (set at startup)
  backend.py           Provider: builds OpenBao + Authentik clients, caches k8s-auth token
  openbao.py           OpenBaoClient (KV v2 via hvac) + kubernetes_login()
  authentik.py         AuthentikClient (wraps the official authentik-client) + OAuth2ProviderSpec
  externalsecrets.py   build()/apply() ExternalSecret via CustomObjectsApi
  argocd.py            build()/apply()/delete() Argo CD Application
  cnpg.py              build()/apply()/delete() CloudNativePG Cluster (+ name helpers)
  workflows.py         build()/apply()/get()/phase()/delete() Argo Workflow
  argoevents.py        build/apply/delete github EventSource + Sensor (previews)
  ingress.py           build/apply/delete the webhook Ingress
  github.py            tiny urllib GitHub client (PR comment + commit status)
  dbjobs.py            ensure snapshot/restore secrets + (pure) workflow params
  buildsecrets.py      ensure Harbor push / git-token / webhook / db / s3 Secrets; read_key
  namespaces.py        ensure()/delete() per-workspace namespaces
  ssoapps.py           build/apply SSOApplication CRs (apply proxy + apply_oauth2)
  odoonotify.py        tiny urllib client: POST CR status to the Odoo portal ingest
  resolve.py           compute() effective config + getters + parse_owner_repo /
                       built_image_ref / app_db_connection / sanitize_default / deep_merge
  apptypes/            adapter interface (base.Ctx) + registry + odoo/superset/mailpit/generic
  integrations/        connector registry (odoo-superset-datasource, odoo-mailpit-smtp)
  conditions.py        mark_ready()/mark_not_ready() write the Ready condition onto patch
  secretgen.py         random_string() for client-id/client-secret
  handlers/
    _common.py         fail() sets Ready=False and raises kopf.TemporaryError
    ssoapplication.py  SSOApplication -> creds + provider + app + ExternalSecret
    organization.py    Organization -> resolved defaults in status (light)
    client.py          Client -> resolved slug in status (light)
    workspace.py       Workspace -> ensures the <client>-<workspace> namespace
    applicationtype.py ApplicationType -> validates the catalog entry (light)
    application.py     Application -> DB + [build] + [restore] + SSO + adapter + integrations + Argo CD
    gitrepository.py   GitRepository -> parsed owner/repo + preview EventSource/Sensor/Ingress
    snapshot.py        Snapshot -> pg_dump Workflow -> object storage; status.location
    odoo_sync.py       on.field(status) per CRD -> push the changed CR to the Odoo portal
deploy/crds/           CustomResourceDefinitions (plain manifests; source of truth)
charts/                Helm chart (templates render the deployment, RBAC, CRDs)
examples/              sample CRs
tests/                 pytest unit tests (pure modules)
```

## How it runs

- The container ENTRYPOINT is `kopf run --standalone -A --liveness=... -m adomi_platform_controller.operator`.
- `operator.py`'s `@kopf.on.startup` loads the Kubernetes config (in-cluster, else kubeconfig),
  builds the `Provider` from `Config.from_env()`, and stores it via `state.set_provider()`.
- Handlers are plain `def` functions (Kopf runs sync handlers in a thread pool), so the
  blocking `requests` / `kubernetes` calls are fine.

## Conventions

- **Handlers** stack `@kopf.on.create` / `@kopf.on.update` / `@kopf.on.resume` on one
  `reconcile()` function. The SSOApplication also has `@kopf.on.delete` (`finalize`).
- **Status**: write through the `patch` kwarg (`patch.status[...] = ...`) and the
  `conditions.mark_ready/mark_not_ready` helpers. Do not mutate `status` directly.
- **Soft failures** (backend not ready, e.g. Authentik flows missing): call `_common.fail(...)`,
  which sets `Ready=False` and raises `kopf.TemporaryError(delay=30)` to requeue. The patch is
  still applied.
- **Finalizer**: defined by the `@kopf.on.delete` handler; Kopf adds/removes it automatically.
  The finalize handler must **never raise** so deletion is never blocked by an unreachable backend.
- **Generate-once credentials**: `OpenBaoClient.ensure_keys` never overwrites existing keys.
- **Backends** are reached only through `state.provider()`; never construct clients in handlers.
- **Resource builders** (`externalsecrets.py`, `argocd.py`, `cnpg.py`, `ssoapps.py`) follow one
  shape: a `@dataclass Spec`, a pure `build(Spec) -> dict`, and idempotent `apply(Spec)` /
  `delete(name, namespace)` over `CustomObjectsApi`. Keep `build()` pure so it is unit-testable.
- **Argo CD owns workloads**: the Application engine must not render Helm or build manifests. It
  builds `helm.valuesObject` (via the type adapter + integrations + `resolve.deep_merge`) and creates
  an Argo CD `Application`. Adapters (`apptypes/`) and connectors (`integrations/`) are pure and
  unit-tested; gates that need kopf primitives live in `handlers/application.py`.
- **Catalog drives behavior; code adapts values**: `ApplicationType` (cluster) declares the chart +
  capabilities + an `adapter` name; the adapter maps platform inputs into that chart's value shape.
  New simple apps can use the `generic` adapter with only a catalog entry (no controller code).
- **Argo Workflows runs builds + DB jobs**: image builds (`source`), DB snapshots, and restores all
  submit a Workflow from a shipped WorkflowTemplate and **poll** its phase via `fail()`-requeues
  (deterministic Workflow name → idempotent; no separate Workflow watch handler). Builds gate the
  deploy on Succeeded; restores gate the deploy and are idempotent via `status.restoredFrom`
  (cnpg-only — never restore over an external DB). Job Secrets (Harbor push, git token, S3 creds,
  DB password) live in the `argo` namespace because the job pods cannot read the user's namespaces;
  jobs reach env databases over cross-namespace service DNS.
- **Effective config layering**: `Organization → Client → Workspace → ApplicationType → Application`,
  with controller `Config` as the base (`resolve.compute`). Database mode is `spec.database.mode` if
  set, else `cnpg` when the type requires a DB, else `none`.

## After making changes

```sh
ruff check src tests        # lint
ruff format src tests       # format
pytest                      # unit tests
```

`deploy/crds/*.yaml` is the **source of truth** for CRDs.
`charts/adomi-platform-controller/templates/crds.yaml` is those files concatenated inside an
`{{- if .Values.installCRDs }}` guard — after editing a CRD, regenerate it:

```sh
{ echo '{{- if .Values.installCRDs }}'; \
  cat deploy/crds/identity.adomi.io_ssoapplications.yaml \
      deploy/crds/platform.adomi.io_organizations.yaml \
      deploy/crds/platform.adomi.io_clients.yaml \
      deploy/crds/platform.adomi.io_workspaces.yaml \
      deploy/crds/platform.adomi.io_applicationtypes.yaml \
      deploy/crds/platform.adomi.io_applications.yaml \
      deploy/crds/platform.adomi.io_gitrepositories.yaml \
      deploy/crds/platform.adomi.io_snapshots.yaml; \
  echo '{{- end }}'; } > charts/adomi-platform-controller/templates/crds.yaml
```

If you add a new CRD or a new resource the controller touches, update the RBAC in
`charts/adomi-platform-controller/templates/rbac.yaml`.

## Local testing

```sh
pip install -e ".[dev]"
kubectl apply -f deploy/crds/
kopf run -A -m adomi_platform_controller.operator   # uses your kubeconfig context
```

For an isolated end-to-end run, use a dedicated [kind](https://kind.sigs.k8s.io/) cluster
with Authentik, OpenBao, and External Secrets installed (or the kubernetes-provisioner platform).

## References

- Kopf: https://kopf.readthedocs.io/
- Kubernetes Python client: https://github.com/kubernetes-client/python
- authentik-client (official, generated): https://pypi.org/project/authentik-client/
- Authentik API: https://docs.goauthentik.io/docs/developer-docs/api/
- hvac (Vault/OpenBao client): https://hvac.readthedocs.io/
- OpenBao (Vault-compatible) KV v2: https://openbao.org/docs/secrets/kv/kv-v2/
