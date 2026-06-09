> [!TIP]
> **Setting up a cluster from scratch?**
>
> This controller is part of the [kubernetes-provisioner](https://github.com/adomi-io/kubernetes-provisioner)
> platform, which turns a bare Kubernetes cluster into a full platform with one command.

# Adomi - Platform Controller

This is a Kubernetes operator that provides a shared CRD for SSO-enabled applications on the Adomi platform.

The goal of this repository is to let a downstream application declare that it needs single sign-on without wiring directly into Authentik, OpenBao, or External Secrets. You describe the intent as a single `SSOApplication` resource and the controller reconciles the backing objects for you.

Each app is a resource you can create, update, and delete like any other Kubernetes object.

> [!NOTE]
> **Backing services**
>
> - [goauthentik/authentik](https://github.com/goauthentik/authentik) for identity provider objects
> - [openbao/openbao](https://github.com/openbao/openbao) for credential storage
> - [external-secrets/external-secrets](https://github.com/external-secrets/external-secrets) for delivering secrets into namespaces

# Highlights

The CRD is a stable platform abstraction. It stays the same even if the systems behind it change, so downstream repos never have to know how Authentik or OpenBao are wired.

- 🔑 [**SSO applications**](#ssoapplication): Declare an app and get OAuth credentials, an Authentik provider and application, and a published Secret, all from a single resource.
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

# How it works

OpenBao is the source of truth for credentials, and Authentik is always made to match what is stored there.

When you create an `SSOApplication`, the controller reads the Authentik API token from OpenBao at `secret/authentik` (key `bootstrap-token`), then generates a `client-id` and `client-secret` and stores them at `secret/<slug>`. Set `credentials.openbaoPath` to choose another path. Existing credentials are left untouched, so they are generated once and never regenerated. The controller creates or updates the Authentik OAuth2 provider and application, matched by name and slug, then writes an `ExternalSecret` that copies the credentials from OpenBao into a Kubernetes Secret in the app's namespace through the shared `ClusterSecretStore` named `openbao`.

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
  conditions.py        Ready status-condition helpers
  secretgen.py         crypto-random credential generation
  operator.py          Kopf startup + handler registration
  handlers/            the SSOApplication reconciler
deploy/crds/           the CustomResourceDefinition (for `kubectl apply`)
charts/                the Helm chart
examples/              sample resources
```

# License

For license details, see the [LICENSE](LICENSE) file in the repository.
