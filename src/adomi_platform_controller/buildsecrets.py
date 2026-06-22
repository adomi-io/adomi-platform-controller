"""Ensures the Secrets a build Workflow needs in the Argo namespace.

A BuildKit build runs in the ``argo`` namespace and needs:
  - a dockerconfigjson Secret to push the built image to Harbor, and
  - optionally a Secret carrying a git token to clone a private source repository.

Both are placed in ``argo`` (the source/credential Secrets the user references live in
their own namespaces, which the build pod cannot read). The push password is read from
OpenBao by the caller and passed in here.
"""

from __future__ import annotations

import base64
import json

from kubernetes import client
from kubernetes.client.exceptions import ApiException

GIT_TOKEN_KEY = "token"  # key used in the git-token Secret


def dockerconfigjson(host: str, username: str, password: str) -> dict:
    """Build a Docker config dict authenticating to one registry host (pure)."""
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()

    return {
        "auths": {
            host: {
                "username": username,
                "password": password,
                "auth": auth,
            },
        },
    }


def _apply_secret(name: str, namespace: str, secret_type: str, string_data: dict) -> None:
    """Create or update an Opaque/dockerconfigjson Secret (idempotent)."""
    api = client.CoreV1Api()

    body = client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels={"app.kubernetes.io/managed-by": "adomi-platform-controller"},
        ),
        type=secret_type,
        string_data=string_data,
    )

    try:
        api.read_namespaced_secret(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise

        api.create_namespaced_secret(namespace, body)

        return

    api.patch_namespaced_secret(name, namespace, body)


def ensure_dockerconfig_secret(
    name: str, namespace: str, host: str, username: str, password: str
) -> None:
    """Ensure a kubernetes.io/dockerconfigjson Secret for pushing to ``host``."""
    payload = json.dumps(dockerconfigjson(host, username, password))

    _apply_secret(
        name,
        namespace,
        "kubernetes.io/dockerconfigjson",
        {".dockerconfigjson": payload},
    )


def ensure_token_secret(name: str, namespace: str, token: str) -> None:
    """Ensure an Opaque Secret holding a git token (key ``token``)."""
    _apply_secret(name, namespace, "Opaque", {GIT_TOKEN_KEY: token})


def ensure_opaque_secret(
    name: str, namespace: str, string_data: dict, create_only: bool = False
) -> None:
    """Ensure an Opaque Secret with the given data.

    With ``create_only`` the Secret is created if absent and otherwise left
    untouched (used for generate-once values like a webhook HMAC secret).
    """
    api = client.CoreV1Api()

    try:
        api.read_namespaced_secret(name, namespace)
        exists = True
    except ApiException as exc:
        if exc.status != 404:
            raise

        exists = False

    if exists and create_only:
        return

    _apply_secret(name, namespace, "Opaque", string_data)


def read_key(name: str, namespace: str, key: str) -> str:
    """Read and base64-decode a single key from a Secret."""
    secret = client.CoreV1Api().read_namespaced_secret(name, namespace)
    raw = (secret.data or {}).get(key)

    if not raw:
        raise RuntimeError(f"secret {namespace}/{name!r} has no key {key!r}")

    return base64.b64decode(raw).decode().strip()


def delete(name: str, namespace: str) -> None:
    """Delete a managed build Secret (no-op if already gone)."""
    api = client.CoreV1Api()

    try:
        api.delete_namespaced_secret(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
