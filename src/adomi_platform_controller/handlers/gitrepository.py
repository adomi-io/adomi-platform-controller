"""GitRepositoryReconciler.

A GitRepository represents an external source repository (GitHub) used as a build
input for environments. The reconciler validates the URL and parses owner/repo.

When ``spec.preview.enabled`` is set, it also provisions the preview pipeline for
that repo: it copies the repo token and generates a webhook HMAC secret into the
argo namespace, then creates an Argo Events github EventSource (which auto-registers
the GitHub webhook), a Sensor (PR action -> create/patch/delete a preview
preview Workspace + Application) and a webhook Ingress. The finalizer tears those down.
"""

from __future__ import annotations

import kopf
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from .. import argoevents, buildsecrets, conditions, ingress, resolve, secretgen, state
from ._common import fail

GROUP = "platform.adomi.io"
VERSION = "v1alpha1"
PLURAL = "gitrepositories"

WEBHOOK_SECRET_LENGTH = 40


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def reconcile(spec, meta, status, patch, name, namespace, **_) -> None:
    generation = meta.get("generation", 0)
    cfg = state.provider().config

    url = (spec.get("url") or "").strip()

    if not url:
        fail(patch, status, conditions.REASON_INVALID_SPEC, "spec.url is required", generation)

    owner, repo = resolve.parse_owner_repo(url)

    if not owner or not repo:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            f"could not parse owner/repo from url {url!r}",
            generation,
        )

    cred_ref = spec.get("credentialsSecretRef") or {}

    if cred_ref.get("name"):
        try:
            client.CoreV1Api().read_namespaced_secret(cred_ref["name"], namespace)
        except ApiException as exc:
            if exc.status == 404:
                fail(
                    patch,
                    status,
                    conditions.REASON_DEPENDENCY_NOT_MET,
                    f"credentials secret {namespace}/{cred_ref['name']!r} not found",
                    generation,
                )

            raise

    patch.status["owner"] = owner
    patch.status["repo"] = repo
    patch.status["url"] = url

    preview = spec.get("preview") or {}

    if preview.get("enabled"):
        webhook_url = _reconcile_preview(
            cfg,
            spec,
            preview,
            owner,
            repo,
            name,
            namespace,
            patch,
            status,
            generation,
        )

        patch.status["previewEventSource"] = argoevents.eventsource_name(owner, repo)
        patch.status["previewSensor"] = argoevents.sensor_name(owner, repo)
        patch.status["webhookURL"] = webhook_url

        msg = f"GitRepository {owner}/{repo} reconciled; previews enabled"
    else:
        # Previews off: tear down any previously-generated preview resources.
        _delete_preview(cfg, owner, repo)

        patch.status["previewEventSource"] = ""
        patch.status["previewSensor"] = ""
        patch.status["webhookURL"] = ""

        msg = f"GitRepository {owner}/{repo} reconciled"

    conditions.mark_ready(patch, status, msg, generation)


def _reconcile_preview(
    cfg, spec, preview, owner, repo, name, namespace, patch, status, generation
) -> str:
    """Provision the preview EventSource/Sensor/Ingress; return the webhook URL."""
    client_ref = (preview.get("clientRef") or {}).get("name")

    if not client_ref:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "spec.preview.clientRef.name is required when previews are enabled",
            generation,
        )

    application_type = preview.get("applicationType") or "odoo"

    cred_ref = spec.get("credentialsSecretRef") or {}

    if not cred_ref.get("name"):
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "spec.credentialsSecretRef is required when previews are enabled "
            "(token needs admin:repo_hook)",
            generation,
        )

    webhook_host = cfg.resolved_webhook_host()

    if not webhook_host:
        fail(
            patch,
            status,
            conditions.REASON_INVALID_SPEC,
            "no webhook host: set platform.preview webhookHost or an Organization base domain",
            generation,
        )

    es_name = argoevents.eventsource_name(owner, repo)
    path = argoevents.webhook_path(owner, repo)
    token_secret = f"{es_name}-token"
    webhook_secret = f"{es_name}-webhook"

    try:
        token = buildsecrets.read_key(cred_ref["name"], namespace, cred_ref.get("key") or "token")
        buildsecrets.ensure_token_secret(token_secret, cfg.argo_namespace, token)
        # Generate the HMAC secret once; never rotate it from under the webhook.
        buildsecrets.ensure_opaque_secret(
            webhook_secret,
            cfg.argo_namespace,
            {"secret": secretgen.random_string(WEBHOOK_SECRET_LENGTH)},
            create_only=True,
        )

        argoevents.apply_eventsource(
            argoevents.EventSourceSpec(
                name=es_name,
                namespace=cfg.argo_namespace,
                owner=owner,
                repo=repo,
                webhook_url=f"https://{webhook_host}",
                webhook_path=path,
                token_secret=token_secret,
                webhook_secret=webhook_secret,
                labels=_labels(owner, repo),
            )
        )
        argoevents.apply_sensor(
            argoevents.SensorSpec(
                name=argoevents.sensor_name(owner, repo),
                namespace=cfg.argo_namespace,
                eventsource_name=es_name,
                service_account=cfg.preview_sensor_service_account,
                owner=owner,
                repo=repo,
                mgmt_namespace=namespace,
                client_ref=client_ref,
                application_type=application_type,
                repository_ref=name,
                base_image=preview.get("baseImage") or "",
                labels=_labels(owner, repo),
            )
        )
        ingress.apply(
            ingress.Spec(
                name=es_name,
                namespace=cfg.argo_namespace,
                host=webhook_host,
                path=path,
                service_name=argoevents.service_name(es_name),
                service_port=argoevents.WEBHOOK_PORT,
                ingress_class_name=cfg.preview_ingress_class,
                tls_secret_name=f"{es_name}-tls",
                cluster_issuer=cfg.cluster_issuer,
                labels=_labels(owner, repo),
            )
        )
    except kopf.TemporaryError:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(
            patch,
            status,
            conditions.REASON_BACKEND_ERROR,
            f"provisioning preview pipeline: {exc}",
            generation,
        )

    return f"https://{webhook_host}{path}"


@kopf.on.delete(GROUP, VERSION, PLURAL)
def finalize(spec, status, logger, **_) -> None:
    """Best-effort teardown of the preview EventSource/Sensor/Ingress + secrets."""
    cfg = state.provider().config
    owner = status.get("owner")
    repo = status.get("repo")

    if not owner or not repo:
        owner, repo = resolve.parse_owner_repo(spec.get("url") or "")

    if owner and repo:
        try:
            _delete_preview(cfg, owner, repo)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed tearing down preview pipeline during finalize: {exc}")


def _delete_preview(cfg, owner: str, repo: str) -> None:
    """Delete the generated preview resources for a repo (idempotent)."""
    es_name = argoevents.eventsource_name(owner, repo)

    argoevents.delete_sensor(argoevents.sensor_name(owner, repo), cfg.argo_namespace)
    argoevents.delete_eventsource(es_name, cfg.argo_namespace)
    ingress.delete(es_name, cfg.argo_namespace)
    buildsecrets.delete(f"{es_name}-token", cfg.argo_namespace)
    buildsecrets.delete(f"{es_name}-webhook", cfg.argo_namespace)


def _labels(owner: str, repo: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "adomi-platform-controller",
        "platform.adomi.io/repo": f"{owner}-{repo}".lower(),
    }
