# Adomi Platform Management (Odoo)

An Odoo project that turns Odoo into the **management portal for the Adomi platform**.
The `adomi_platform` addon mirrors the `platform.adomi.io` CRDs as Odoo models and
keeps them in sync with Kubernetes: creating/editing a record applies the matching
custom resource, and the platform controller **pushes** live status back into Odoo
as it changes (a manual button + a fallback cron cover the rest).

Built on the [Adomi Odoo community-base image](https://github.com/adomi-io/odoo-community-base)
following the [boilerplate](https://github.com/adomi-io/boilerplate-odoo) layout.

## What it manages

| Odoo model | CRD (`platform.adomi.io/v1alpha1`) | Scope |
|------------|------------------------------------|-------|
| Organization | `Organization` | cluster |
| Client | `Client` | namespaced |
| Workspace | `Workspace` | namespaced |
| Application | `Application` (+ integrations) | namespaced |
| Application Type | `ApplicationType` (catalog; importable) | cluster |
| Git Repository | `GitRepository` | namespaced |
| Snapshot | `Snapshot` | namespaced |

Each model carries a **Resource name** (`metadata.name`), a **Status** (from the CR's
`Ready` condition) and a **Sync from cluster** button. Odoo is the source of truth for
what you create; status fields (URL, phase, namespace, …) are reported back from the
cluster. The catalog (`ApplicationType`) is owned by the cluster — use **Import from cluster**.

## How sync works

The **write backend** (`adomi_platform.write_backend`) decides where create/edit/delete
go. `api` makes git the durable source of truth via the platform API; `kubernetes` is
the legacy direct-apply (and the offline/local-dev default).

```
write_backend = api  (recommended — git is the source of truth)
  Odoo record  ──create/write──►  PUT  /v1/tenants/<client>/<plural>/<name>  (platform API)
  Odoo record  ──unlink───────►  DELETE /v1/tenants/<client>/<plural>/<name>
                                  → the API commits the CR to the customer's Forgejo repo
                                  → Argo CD reconciles the repo into the cluster

write_backend = kubernetes  (legacy / offline)
  Odoo record  ──create/write──►  apply CR (CustomObjectsApi, in-cluster SA or kubeconfig)
  Odoo record  ──unlink───────►  delete CR

both backends:
  controller  ──status change──►  POST /adomi_platform/ingest → update Odoo (Ready, phase, url, …)
  Sync button / fallback cron ─►  read CR status → update Odoo (when a push was missed)
```

In `api` mode the portal is a thin client: it sends each customer-owned record's
**intent** (the CR `spec`) to the [platform API](https://github.com/adomi-io/adomi-platform-api),
which owns the Forgejo credentials and the repo / namespace / kind conventions — it
builds the full CR and commits it to that customer's tenant repo (one repo per
`Client`, named after the slug). Cluster-scoped, platform-owned resources
(`Organization`, the base `ApplicationType` catalog) always take the Kubernetes path.
The API endpoint comes from `ADOMI_PLATFORM_API_URL` (env) or `adomi_platform.api_url`;
the bearer token from `ADOMI_PLATFORM_API_TOKEN`. `ADOMI_WRITE_BACKEND` overrides the
backend so in-cluster runs as `api` while local dev stays `kubernetes`.

Status flows **into** Odoo two ways regardless of backend: the platform controller POSTs each resource to
`/adomi_platform/ingest` the moment its status changes (real-time, no polling), and a
manual **Sync** button / hourly fallback cron reconcile anything missed while the portal
was down. The ingest endpoint is authenticated with a shared bearer token
(`ADOMI_INGEST_TOKEN`) that must match the token the controller reads from OpenBao.

The addon uses the `kubernetes` Python client (installed in the image). It loads
in-cluster config when running on the platform, otherwise a mounted kubeconfig.
The target namespace and a global on/off switch are system parameters:

- `adomi_platform.namespace` (default `adomi-system`)
- `adomi_platform.sync_enabled` (`1`/`0` — set `0` for offline Odoo-only editing)

## Onboarding & deploying

The portal is organised around the **customer** (the `Client` CR). The landing page
is a **Customers** kanban where each card rolls up that customer's estate — app
health (ready/total), workspace count — with a one-click **Deploy app** button. Open
a customer to get a dashboard: smart buttons to its Applications and Workspaces, an
embedded board of every app across all its workspaces (live status + URLs), and a
**Deploy Application** header button pre-scoped to that customer.

Deploying uses a one-screen guided flow (**Adomi Platform ▸ Deploy Application**, or
the per-customer buttons): pick or name a Customer, pick or name a Workspace
(dev/prod/pdi/…), then **choose an application from the visual catalog** (a custom
Owl widget that renders the cluster's `ApplicationType` catalog as selectable cards
with capability badges), and name the app. The wizard creates only the records that
don't exist yet and drops you on the new Application; everything else defaults from
the type, with overrides under **Advanced**. The Applications view defaults to a
**kanban** grouped by customer, with search filters/group-by for customer, workspace,
type and health.

## Live updates, deep links & observability

- **Live updates:** forms refresh themselves over Odoo's websocket bus the moment the
  controller reports a change (no manual reload). Unsaved edits show a refresh banner
  instead of being clobbered.
- **Deep links:** each Application has smart buttons to open the live app, its Argo CD
  application, Grafana, namespace logs, and Harbor — derived from the platform base
  domain (override hosts via `adomi_platform.{argocd,grafana,harbor}_host`).
- **Observability:** the Application's **Observability** tab shows CPU/memory
  sparklines (Prometheus) and recent logs (Loki) queried server-side over the
  in-cluster services — nothing embedded or exposed. Endpoints default to the
  monitoring-namespace services and are overridable:
  - `adomi_platform.prometheus_url`, `adomi_platform.loki_url`

## Local development

```sh
cp .env.example .env
# To sync against a real cluster, uncomment the kubeconfig mount in docker-compose.yml.
docker compose up --build
# Odoo at http://localhost:8069 — the setup hook installs `adomi_platform`.
```

Without a cluster, set `adomi_platform.sync_enabled = 0` (Settings ▸ Technical ▸
System Parameters) to edit records without pushing.

## In-cluster deployment

The image is `ghcr.io/adomi-io/adomi-platform-management`. When deployed onto the
platform it needs a ServiceAccount bound to a ClusterRole that can read/write the
`platform.adomi.io` resources (same verbs the controller's UI used). See
`deploy/rbac.example.yaml`.

## Single sign-on (Authentik / OIDC)

Sign-in uses OpenID Connect against Authentik via OCA `auth_oidc` (bundled in the
`odoo-community-base` image). The wiring is automatic in-cluster:

1. The provisioner declares an `SSOApplication` (`adomi-platform-management`, oauth2).
   The platform controller reconciles it into an Authentik OAuth2 provider/application
   and publishes the `management-sso` Secret (`client-id` / `client-secret`) into the
   portal namespace.
2. The deployment exposes those as env (`ADOMI_OIDC_CLIENT_ID` / `_SECRET`) plus
   `ADOMI_OIDC_AUTH_HOST`, `ADOMI_OIDC_APP_SLUG`, `ADOMI_PORTAL_BASE_URL`.
3. On install/upgrade the addon seeds an `auth.oauth.provider` ("Authentik") from
   those env vars — authorization-code (OIDC) flow, endpoints built from the auth host
   and app slug — and sets/freezes `web.base.url`. A **Log in with Authentik** button
   then appears on the login page.

> First sign-in only succeeds for an Odoo user linked to the Authentik identity (by
> `oauth_uid`), unless you enable OAuth signup. To auto-provision portal users from
> Authentik, enable signup (Settings ▸ Users) — Authentik already gates who can open
> the application, so this is reasonable for an internal admin portal. Local admin
> login remains as break-glass.

### Roles from Authentik groups

The SSOApplication requests the `groups` scope, so the id_token carries the user's
Authentik groups. On every OIDC login the addon syncs Odoo access from that claim:

- any OIDC user becomes an internal user (`base.group_user`);
- members of the **Platform Admins** Authentik group get Settings/admin access
  (`base.group_system`); non-members are removed from it.

So access levels come from Authentik group membership, not manual per-user setup —
add/remove people in the **Platform Admins** group in Authentik. The admin group name
is configurable via `adomi_platform.oidc_admin_group`. The bootstrap admin
(`base.user_admin`) is never altered, so a local break-glass login always remains.
