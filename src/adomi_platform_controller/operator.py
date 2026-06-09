"""Operator entry point: Kopf startup configuration and handler registration.

Run with::

    kopf run -A -m adomi_platform_controller.operator

Importing this module registers all reconcile handlers (via the ``handlers``
package) so ``kopf run -m`` picks them up.
"""

from __future__ import annotations

import kopf
from kubernetes import config as k8s_config

from .backend import Provider
from .config import Config
from . import state

# Importing the handlers package registers the @kopf.on.* handlers.
from . import handlers  # noqa: E402,F401  (import for side effects)

# Finalizer used on SSOApplications so backend cleanup runs before deletion.
SSO_FINALIZER = "identity.adomi.io/ssoapplication-cleanup"


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, logger, **_) -> None:
    """Configure the Kubernetes client and build the backend Provider."""
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    cfg = Config.from_env()
    state.set_provider(Provider(cfg))

    # Use a stable, descriptive finalizer name on resources we clean up.
    settings.persistence.finalizer = SSO_FINALIZER
    # Post Kubernetes Events for handler outcomes (visible in `kubectl describe`).
    settings.posting.enabled = True

    logger.info(
        "Backend configured",
        extra={
            "openbao": cfg.openbao_addr,
            "authMode": cfg.auth_mode.value,
            "authentik": cfg.authentik_addr,
            "store": cfg.cluster_secret_store,
        },
    )