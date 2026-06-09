# adomi-platform-controller agent guide

A [Kopf](https://kopf.readthedocs.io/)-based Python Kubernetes operator. It
reconciles a single CRD, `SSOApplication`, into Authentik, OpenBao, and
External Secrets, with OpenBao as the source of truth for credentials.

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
  conditions.py        mark_ready()/mark_not_ready() write the Ready condition onto patch
  secretgen.py         random_string() for client-id/client-secret
  handlers/
    _common.py         fail() sets Ready=False and raises kopf.TemporaryError
    ssoapplication.py  SSOApplication -> creds + provider + app + ExternalSecret
deploy/crds/           CustomResourceDefinition (plain manifest)
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

## After making changes

```sh
ruff check src tests        # lint
ruff format src tests       # format
pytest                      # unit tests
```

If you change a CRD's schema, edit **both** `deploy/crds/<name>.yaml` and the matching block
in `charts/adomi-platform-controller/templates/crds.yaml` (they are kept in sync by hand).
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
