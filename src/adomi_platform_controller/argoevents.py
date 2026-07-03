"""Builds and applies Argo Events EventSource and Sensor objects for previews.

When a GitRepository enables previews, the controller generates, in the argo
namespace:
  - a github EventSource that auto-registers the GitHub webhook and validates the
    HMAC signature, emitting an event per pull_request action, and
  - a Sensor whose triggers create / patch / delete a preview Environment and an Odoo
    Application (built from the PR) in the management namespace.

The Application then builds-from-source and deploys via the engine. We use the
dynamic CustomObjectsApi; Argo Events installs the CRDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GROUP = "argoproj.io"
VERSION = "v1alpha1"
PLURAL_EVENTSOURCES = "eventsources"
PLURAL_SENSORS = "sensors"

EVENT_KEY = "pr"
WEBHOOK_PORT = 12000

# Annotations carried on a preview Application so the engine can report back to the PR.
ANN_REPO = "platform.adomi.io/repo"
ANN_PR_NUMBER = "platform.adomi.io/pr-number"
ANN_COMMIT_SHA = "platform.adomi.io/commit-sha"

PLATFORM_API = "platform.adomi.io/v1alpha1"


def _slug(owner: str, repo: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", f"{owner}-{repo}".lower()).strip("-")


def eventsource_name(owner: str, repo: str) -> str:
    return f"gh-{_slug(owner, repo)}"[:253]


def sensor_name(owner: str, repo: str) -> str:
    return f"gh-{_slug(owner, repo)}"[:253]


def webhook_path(owner: str, repo: str) -> str:
    return f"/{_slug(owner, repo)}"


def service_name(es_name: str) -> str:
    return f"{es_name}-eventsource-svc"


@dataclass
class EventSourceSpec:
    name: str
    namespace: str
    owner: str
    repo: str
    webhook_url: str
    webhook_path: str
    token_secret: str
    webhook_secret: str
    labels: dict[str, str] | None = None


def build_eventsource(s: EventSourceSpec) -> dict:
    metadata: dict = {
        "name": s.name,
        "namespace": s.namespace,
    }

    if s.labels:
        metadata["labels"] = s.labels

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "EventSource",
        "metadata": metadata,
        "spec": {
            "service": {
                "ports": [
                    {
                        "port": WEBHOOK_PORT,
                        "targetPort": WEBHOOK_PORT,
                    },
                ],
            },
            "github": {
                EVENT_KEY: {
                    "repositories": [
                        {
                            "owner": s.owner,
                            "names": [s.repo],
                        },
                    ],
                    "webhook": {
                        "endpoint": s.webhook_path,
                        "port": str(WEBHOOK_PORT),
                        "method": "POST",
                        "url": s.webhook_url,
                    },
                    "events": ["pull_request"],
                    "apiToken": {
                        "name": s.token_secret,
                        "key": "token",
                    },
                    "webhookSecret": {
                        "name": s.webhook_secret,
                        "key": "secret",
                    },
                    "active": True,
                    "insecure": False,
                }
            },
        },
    }


@dataclass
class SensorSpec:
    name: str
    namespace: str  # argo namespace (where the Sensor runs)
    eventsource_name: str
    service_account: str
    owner: str
    repo: str
    mgmt_namespace: str  # namespace the Environment/Application CRs are created in
    client_ref: str  # Client the preview belongs to
    application_type: str  # the ApplicationType to run (e.g. "odoo")
    repository_ref: str  # GitRepository CR name (for Application.source)
    base_image: str = ""
    labels: dict[str, str] | None = None


def _esc(key: str) -> str:
    return key.replace(".", "\\.")


def _environment_resource(s: SensorSpec) -> dict:
    return {
        "apiVersion": PLATFORM_API,
        "kind": "Environment",
        "metadata": {
            "name": "pr-0",  # overwritten -> pr-<number>
            "namespace": s.mgmt_namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "adomi-platform-controller",
            },
        },
        "spec": {
            "clientRef": {
                "name": s.client_ref,
            },
            "class": "preview",
        },
    }


def _app_resource(s: SensorSpec) -> dict:
    source: dict = {
        "repositoryRef": {
            "name": s.repository_ref,
        },
        "ref": "",
    }

    if s.base_image:
        source["baseImage"] = s.base_image

    return {
        "apiVersion": PLATFORM_API,
        "kind": "Application",
        "metadata": {
            "name": "pr-0-app",  # overwritten -> pr-<number>-<type>
            "namespace": s.mgmt_namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "adomi-platform-controller",
                "platform.adomi.io/preview": "true",
            },
            "annotations": {
                ANN_REPO: f"{s.owner}/{s.repo}",  # static
                ANN_PR_NUMBER: "0",  # overwritten
                ANN_COMMIT_SHA: "",  # overwritten
            },
        },
        "spec": {
            "environmentRef": {
                "name": "pr-0",
            },  # overwritten
            "type": s.application_type,
            "source": source,
        },
    }


def _dep(name: str, es: str, actions: list[str]) -> dict:
    return {
        "name": name,
        "eventSourceName": es,
        "eventName": EVENT_KEY,
        "filters": {
            "data": [
                {
                    "path": "body.action",
                    "type": "string",
                    "comparator": "=",
                    "value": actions,
                },
            ],
        },
    }


def _tpl_ws_name(dep: str) -> dict:
    return {
        "src": {
            "dependencyName": dep,
            "dataTemplate": "pr-{{ .Input.body.pull_request.number }}",
        },
        "dest": "metadata.name",
    }


def _tpl_app_name(dep: str, app_type: str) -> dict:
    return {
        "src": {
            "dependencyName": dep,
            "dataTemplate": "pr-{{ .Input.body.pull_request.number }}-" + app_type,
        },
        "dest": "metadata.name",
    }


def build_sensor(s: SensorSpec) -> dict:
    metadata: dict = {
        "name": s.name,
        "namespace": s.namespace,
    }

    if s.labels:
        metadata["labels"] = s.labels

    app_create_params = [
        _tpl_app_name("pr-open", s.application_type),
        {
            "src": {
                "dependencyName": "pr-open",
                "dataTemplate": "pr-{{ .Input.body.pull_request.number }}",
            },
            "dest": "spec.environmentRef.name",
        },
        {
            "src": {
                "dependencyName": "pr-open",
                "dataKey": "body.pull_request.head.sha",
            },
            "dest": "spec.source.ref",
        },
        {
            "src": {
                "dependencyName": "pr-open",
                "dataTemplate": "{{ .Input.body.pull_request.number }}",
            },
            "dest": f"metadata.annotations.{_esc(ANN_PR_NUMBER)}",
        },
        {
            "src": {
                "dependencyName": "pr-open",
                "dataKey": "body.pull_request.head.sha",
            },
            "dest": f"metadata.annotations.{_esc(ANN_COMMIT_SHA)}",
        },
    ]
    app_sync_params = [
        _tpl_app_name("pr-sync", s.application_type),
        {
            "src": {
                "dependencyName": "pr-sync",
                "dataKey": "body.pull_request.head.sha",
            },
            "dest": "spec.source.ref",
        },
        {
            "src": {
                "dependencyName": "pr-sync",
                "dataKey": "body.pull_request.head.sha",
            },
            "dest": f"metadata.annotations.{_esc(ANN_COMMIT_SHA)}",
        },
    ]

    def k8s(operation, resource, parameters, *, patch=False):
        block = {
            "operation": operation,
            "source": {
                "resource": resource,
            },
            "parameters": parameters,
        }

        if patch:
            block["patchStrategy"] = "application/merge-patch+json"

        return block

    triggers = [
        {
            "template": {
                "name": "create-environment",
                "conditions": "pr-open",
                "k8s": k8s("create", _environment_resource(s), [_tpl_ws_name("pr-open")]),
            }
        },
        {
            "template": {
                "name": "create-app",
                "conditions": "pr-open",
                "k8s": k8s("create", _app_resource(s), app_create_params),
            }
        },
        {
            "template": {
                "name": "sync-app",
                "conditions": "pr-sync",
                "k8s": k8s("patch", _app_resource(s), app_sync_params, patch=True),
            }
        },
        {
            "template": {
                "name": "delete-app",
                "conditions": "pr-close",
                "k8s": k8s(
                    "delete",
                    _app_resource(s),
                    [_tpl_app_name("pr-close", s.application_type)],
                ),
            }
        },
        {
            "template": {
                "name": "delete-environment",
                "conditions": "pr-close",
                "k8s": k8s("delete", _environment_resource(s), [_tpl_ws_name("pr-close")]),
            }
        },
    ]

    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "Sensor",
        "metadata": metadata,
        "spec": {
            "template": {
                "serviceAccountName": s.service_account,
            },
            "dependencies": [
                _dep("pr-open", s.eventsource_name, ["opened", "reopened"]),
                _dep("pr-sync", s.eventsource_name, ["synchronize"]),
                _dep("pr-close", s.eventsource_name, ["closed"]),
            ],
            "triggers": triggers,
        },
    }


# --- apply / delete --------------------------------------------------------------


def _apply(plural: str, namespace: str, name: str, desired: dict) -> None:
    from kubernetes import client
    from kubernetes.client.exceptions import ApiException

    api = client.CustomObjectsApi()

    try:
        api.get_namespaced_custom_object(GROUP, VERSION, namespace, plural, name)
    except ApiException as exc:
        if exc.status != 404:
            raise

        api.create_namespaced_custom_object(GROUP, VERSION, namespace, plural, desired)

        return

    api.patch_namespaced_custom_object(GROUP, VERSION, namespace, plural, name, desired)


def _delete(plural: str, namespace: str, name: str) -> None:
    from kubernetes import client
    from kubernetes.client.exceptions import ApiException

    api = client.CustomObjectsApi()

    try:
        api.delete_namespaced_custom_object(GROUP, VERSION, namespace, plural, name)
    except ApiException as exc:
        if exc.status != 404:
            raise


def apply_eventsource(s: EventSourceSpec) -> None:
    _apply(PLURAL_EVENTSOURCES, s.namespace, s.name, build_eventsource(s))


def apply_sensor(s: SensorSpec) -> None:
    _apply(PLURAL_SENSORS, s.namespace, s.name, build_sensor(s))


def delete_eventsource(name: str, namespace: str) -> None:
    _delete(PLURAL_EVENTSOURCES, namespace, name)


def delete_sensor(name: str, namespace: str) -> None:
    _delete(PLURAL_SENSORS, namespace, name)
