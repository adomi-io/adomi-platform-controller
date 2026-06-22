> [!TIP]
> **Setting up a cluster from scratch?**
>
> This controller is part of the [kubernetes-provisioner](https://github.com/adomi-io/kubernetes-provisioner)
> platform, which turns a bare Kubernetes cluster into a full platform with one command.

# Adomi - Platform Controller

This is a Kubernetes operator that provides a shared CRD for SSO-enabled applications on the Adomi platform.

The goal of this repository is to let a downstream application declare that it needs single sign-on without wiring directly into Authentik, OpenBao, or External Secrets. You describe the intent as a single `SSOApplication` resource and the controller reconciles the backing objects for you.

Each app is a resource you can create, update, and delete like any other Kubernetes object.

## The control plane: two components, one repo

This repo is the platform **control plane** and ships two images from one codebase that
share a single resource schema (`src/adomi_platform_schema`):

- **`adomi_platform_api`** — the **front door**. End users drive the platform through it
  (directly, or via the Odoo portal, the CLI, or partner UIs). It speaks the **same
  object language as the operator** — one API per controller object: `clients`,
  `domains`, `databases`, `workspaces`, `applications`, `gitrepositories`, `snapshots`,
  nested under the owning client (`/v1/clients/{client}/...`). Each `PUT`/`DELETE` turns
  the request into that object's `platform.adomi.io` custom resource and **commits it to
  the client's tenant git repo** (Forgejo), which Argo CD reconciles; each `GET` returns
  the resource's live status read from the cluster. A FastAPI service: `routers/` →
  `service` → `git/` writer (+ `cluster` reader). Built from `Dockerfile.api`; chart in
  `charts/adomi-platform-api`.
- **`adomi_platform_controller`** — the **operator** (Kopf). It reconciles the committed
  CRs (and `SSOApplication`s) into running infrastructure: Authentik, OpenBao, CNPG,
  Argo CD apps, ingress, builds. Built from `Dockerfile`; chart in
  `charts/adomi-platform-controller`.

Ephemeral resources (PR preview environments) are the deliberate exception — the
controller creates them in-cluster directly, not through the API/git.

> [!NOTE]
> **Backing services**
>
> - [goauthentik/authentik](https://github.com/goauthentik/authentik) for identity provider objects
> - [openbao/openbao](https://github.com/openbao/openbao) for credential storage
> - [external-secrets/external-secrets](https://github.com/external-secrets/external-secrets) for delivering secrets into namespaces

# Highlights

The CRD is a stable platform abstraction. It stays the same even if the systems behind it change, so downstream repos never have to know how Authentik or OpenBao are wired.

- 🔑 [**SSO applications**](#ssoapplication): Declare an app and get OAuth credentials, an Authentik provider and application, and a published Secret, all from a single resource.
- 🏭 [**Application platform**](#platform-resources): Declare a `Client`, `Workspace`, and `Application`; the controller runs any catalog app (Odoo, Mailpit, Superset, …) with a database, SSO, and ingress via Argo CD — and auto-integrates them.
- 🔐 [**OpenBao as the source of truth**](#how-it-works): Credentials are generated once in OpenBao and never regenerated. Authentik is always made to match.
- 🤝 [**Drop-in for the provisioner**](#getting-started): Every default matches the kubernetes-provisioner, so a default install needs no extra configuration.
- ♻️ **Idempotent**: The resource reports a `Ready` status condition and can be reconciled as many times as you like.

# Getting started

> [!WARNING]
> This controller talks to Authentik, OpenBao, and External Secrets. It expects the
> [kubernetes-provisioner](https://github.com/adomi-io/kubernetes-provisioner) services to be
> running, or the equivalent services reachable in your cluster.

Install the CRDs and the controller with Helm:

```sh
helm upgrade --install adomi-platform-controller \
  charts/adomi-platform-controller \
  --namespace adomi-system --create-namespace
```

Declare an application (create its namespace first, if needed):

```sh
kubectl create namespace example
kubectl apply -f examples/ssoapplication.yaml
```

Watch it become ready:

```sh
kubectl get ssoapplications -n example
```

When the `SSOApplication` reports `Ready`, Authentik holds the provider and application, and a Secret with the OAuth `client-id` and `client-secret` is published into the app's namespace.

# Resources

## SSOApplication

An `SSOApplication` is an app that needs single sign-on, and it is the only resource you create. The controller does the full setup: it generates the OAuth credentials, reconciles the Authentik objects, and publishes the credentials.

```yaml
apiVersion: identity.adomi.io/v1alpha1
kind: SSOApplication
metadata:
  name: app-prod
  namespace: example
spec:
  displayName: Example App
  protocol: oauth2
  redirectUris:
    - https://app.example.com/oauth/callback
  credentials:
    targetSecret:
      name: app-oauth
```

When this resource is created, the controller generates OAuth credentials, sets up the Authentik provider and application, and publishes the credentials into a Secret named `app-oauth` in the `example` namespace.

> [!TIP]
> To reuse credentials already stored at a specific path, set `credentials.openbaoPath` to that
> path (for example `argo-workflows`) and the controller reads the existing
> `secret/argo-workflows` instead of generating new ones. See [examples/argo-workflows.yaml](./examples/argo-workflows.yaml).

### Proxy providers (forward-auth)

Set `protocol: proxy` for an app with no native SSO. The controller reconciles an Authentik
proxy provider and application and attaches the provider to an outpost (the built-in embedded
outpost unless `proxy.outpost` names another). Point a reverse proxy's forward-auth at that
outpost to require sign-in. No credentials are generated or published - the proxy client is
owned by Authentik.

```yaml
spec:
  protocol: proxy
  proxy:
    mode: forwardDomain          # forwardSingle | forwardDomain | proxy
    externalHost: https://auth.example.com
    cookieDomain: example.com
```

See [examples/ssoapplication-proxy.yaml](./examples/ssoapplication-proxy.yaml).

# Platform resources

Beyond SSO, the controller is a generic, multi-tenant **application platform**. You provision a
customer, give them **workspaces**, and run a subset of catalog **applications** (Odoo, Mailpit,
Superset, …) in each. You describe *what* you want; the controller reconciles the supporting
objects (namespace, database, SSO, ingress) and hands the workload to **Argo CD**.

```text
Organization        cluster-wide defaults (base domain, image repo)
  └── Client        an end customer (e.g. Example Co)
        └── Workspace        a named env: production | development | pdi | preview | test
              └── Application "run <type> here" — deploys a catalog chart via Argo CD

ApplicationType     the catalog: chart source + adapter + capabilities (cluster-scoped)
GitRepository       an external source repo (build input; optional PR preview environments)
Snapshot            a point-in-time dump of an Application's database (clone source)
```

One operator, not many: ~80% of "run app X for client Y" is shared (namespace + database + SSO +
ingress + Argo CD Application). An **ApplicationType** (the catalog) declares each app's chart and an
**adapter** name; a small code adapter (`odoo`/`superset`/`mailpit`/`generic`) maps the platform's
standard inputs into that chart's value shape — so charts can be ours (adomi-helm) or upstream
(apache/superset). The provisioner ships the catalog.

Creating an `Application` makes the controller: resolve `Organization → Client → Workspace →
ApplicationType` config; provision the database (`none` | in-cluster **CloudNativePG** | `external`);
declare an `SSOApplication` (native OIDC `oauth2`, or forward-auth `proxy`); run the adapter +
integrations to build the Helm values; create the **Argo CD `Application`**; and publish a
**connection contract** (`status.connection`) other apps integrate with.

```yaml
apiVersion: platform.adomi.io/v1alpha1
kind: Workspace
metadata: { name: production, namespace: adomi-system }
spec:
  clientRef: { name: acme }
  class: production
---
apiVersion: platform.adomi.io/v1alpha1
kind: Application
metadata: { name: odoo, namespace: adomi-system }
spec:
  workspaceRef: { name: production }
  type: odoo
  database: { mode: cnpg }
  odoo: { version: "19.0", workers: 2 }
  sso: { enabled: true }
```

See [examples/](./examples/) for `Organization`, `Client`, `Workspace`, and `application-*`
manifests.

### Auto-integration between apps

Apps wire to each other declaratively. The consumer lists `spec.integrations`; when the provider is
Ready and has published its connection contract, a connector keyed by `type` injects the right
values. For example, register an Odoo database as a Superset data source:

```yaml
kind: Application
spec:
  type: superset
  database: { mode: cnpg }
  integrations:
    - type: odoo-superset-datasource
      fromRef: { name: odoo }     # the provider Application
```

Connectors are a small registry (provider publishes / consumer references — the same pattern as the
SSO and CNPG secrets). `odoo-mailpit-smtp` similarly routes a dev Odoo's outbound mail to a Mailpit
trap.

### Building from source

An Odoo `Application` can declare a `source` (a `GitRepository` + git ref). The controller runs an
**Argo Workflow** (rootless BuildKit) that builds the repo's Dockerfile, pushes to **Harbor**, gates
the deploy on a successful build, and deploys the built image. This is the foundation for preview
environments. Set `spec.preview.enabled` on a `GitRepository` (with a `clientRef` and a token with
`admin:repo_hook`) and the controller wires the whole PR → preview flow via **Argo Events**: a PR
opened creates a `pr-<n>` Workspace + Application (built from the PR head), synchronize rebuilds,
closed tears it down, and the preview URL is posted back to the PR. See
[examples/gitrepository-previews.yaml](./examples/gitrepository-previews.yaml).

### Database snapshots & cloning

A `Snapshot` captures an Application's Postgres database to object storage (SeaweedFS S3) via an Argo
Workflow (`pg_dump` → upload). Another Application can **clone** it with `restoreFrom`: before
deploying, a restore Workflow runs `download → pg_restore → optional neutralize` into the
freshly-provisioned (cnpg) database. `sanitize` runs Odoo's **neutralize** to disarm mail/crons/
payment keys (default on for non-production), so production data is safe in dev/PDI/preview.
Restoring is `cnpg`-only and idempotent (`status.restoredFrom`). See
[examples/snapshot.yaml](./examples/snapshot.yaml).

# How it works

OpenBao is the source of truth for credentials, and Authentik is always made to match what is stored there.

When you create an OAuth2 `SSOApplication`, the controller reads the Authentik API token from OpenBao at `secret/authentik` (key `bootstrap-token`), then generates a `client-id` and `client-secret` and stores them at `secret/<slug>`. Set `credentials.openbaoPath` to choose another path. Existing credentials are left untouched, so they are generated once and never regenerated. The controller creates or updates the Authentik OAuth2 provider and application, matched by name and slug, then writes an `ExternalSecret` that copies the credentials from OpenBao into a Kubernetes Secret in the app's namespace through the shared `ClusterSecretStore` named `openbao`. A `proxy` application skips the credential and ExternalSecret steps and instead reconciles a proxy provider and attaches it to an outpost.

Default addresses, secret paths, flow slugs, and the signing key name match the provisioner, so a default install needs no extra configuration.

# OpenBao authentication

The controller writes to OpenBao, so it needs more than the read-only access External Secrets uses. There are two ways to authenticate, set with `backend.openbao.authMode`.

**Kubernetes mode** is the default and the recommended one. The controller logs in with its own ServiceAccount using OpenBao's Kubernetes auth, so no static token is ever stored. OpenBao hands back a short-lived token that the controller renews on its own. This needs an OpenBao role and policy, which `openbao-bootstrap` creates: a role named `adomi-platform-controller` bound to the controller's ServiceAccount, with a policy that can read the Authentik token and write the credential paths.

> [!NOTE]
> The OpenBao role binds an exact ServiceAccount name and namespace. Install the controller into
> the `adomi-system` namespace with the default ServiceAccount name (`adomi-platform-controller`)
> so it matches the role that `openbao-bootstrap` creates.

**Token mode** reads a static token from a Kubernetes Secret instead. By default this is the `root-token` from the `openbao-keys` Secret. It is the quickest way to get going, but the root token is broad, so use it only for local testing or a first bring-up. In this mode the chart grants a small Role that can read only that one Secret.

# Configuration

Every backend setting is an environment variable on the controller and a value in the Helm chart under `backend`. Browse [config.py](./src/adomi_platform_controller/config.py) for the full list of variables and defaults, or [values.yaml](./charts/adomi-platform-controller/values.yaml) for the chart values.

| Helm value | Default |
|------------|---------|
| `backend.openbao.address` | `http://openbao.openbao.svc.cluster.local:8200` |
| `backend.openbao.kvMount` | `secret` |
| `backend.openbao.authMode` | `token` |
| `backend.authentik.address` | `http://authentik-server.authentik.svc.cluster.local` |
| `backend.authentik.secretPath` | `authentik` |
| `backend.externalSecrets.clusterSecretStore` | `openbao` |

# Development

This is a Python operator built on [Kopf](https://kopf.readthedocs.io/), the
official [Kubernetes Python client](https://github.com/kubernetes-client/python),
the official [authentik-client](https://pypi.org/project/authentik-client/), and
[hvac](https://hvac.readthedocs.io/) for OpenBao.

```sh
pip install -e ".[dev]"     # install the operator and dev tools
pre-commit install          # format and lint on every commit
pytest                      # run the unit tests
ruff check src tests        # lint
ruff format src tests       # format

# Run locally against your current kubeconfig context:
kubectl apply -f deploy/crds/
kopf run -A -m adomi_platform_controller.operator
# (or, equivalently: python -m adomi_platform_controller)

# Build the image:
docker build -t ghcr.io/adomi-io/adomi-platform-controller:dev .
```

Backend wiring is read from environment variables (see
[`config.py`](src/adomi_platform_controller/config.py)); the Helm chart sets them
from its `backend` values.

The code is laid out like this:

```text
src/adomi_platform_controller/
  config.py            backend configuration from environment variables
  backend.py           builds authenticated OpenBao + Authentik clients
  openbao.py           OpenBao KV v2 access via hvac (+ kubernetes-auth login)
  authentik.py         Authentik access via the official authentik-client
  externalsecrets.py   builds/applies ExternalSecret objects
  argocd.py            builds/applies Argo CD Application objects
  cnpg.py              builds/applies CloudNativePG Cluster objects
  workflows.py         builds/submits Argo Workflows (image builds, db jobs)
  argoevents.py        builds/applies Argo Events EventSource + Sensor (previews)
  ingress.py           builds/applies the webhook Ingress
  github.py            tiny GitHub REST client (PR comment + commit status)
  dbjobs.py            snapshot/restore secret-ensuring + workflow params
  buildsecrets.py      ensures Harbor push / git-token / webhook / db / s3 Secrets in argo
  namespaces.py        ensures/deletes per-workspace namespaces
  ssoapps.py           builds/applies SSOApplication objects (oauth2 + proxy)
  resolve.py           resolves effective config (org→client→workspace→application)
  apptypes/            per-app value adapters (odoo, superset, mailpit, generic) + registry
  integrations/        connector registry (odoo→superset datasource, odoo→mailpit smtp)
  conditions.py        Ready status-condition helpers
  secretgen.py         crypto-random credential generation
  operator.py          Kopf startup + handler registration
  handlers/            reconcilers: ssoapplication, organization, client, workspace,
                       applicationtype, application, gitrepository, snapshot
deploy/crds/           the CustomResourceDefinitions (for `kubectl apply`)
charts/                the Helm chart
examples/              sample resources
```

# License

For license details, see the [LICENSE](LICENSE) file in the repository.
