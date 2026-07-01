"""Builds the Helm values for an app's per-app chart from the explicit intent.

The platform is "nothing inferred": the Application intent carries explicit
``databases`` / ``sso`` / ``env`` lists and an ``ingress`` block. This pure function
maps them onto the per-app chart's value contract (the platform-lib ``databases`` /
``sso`` lists that become capability CRs, plus the workload's ``env`` / ``ingress``).
The chart emits the capability CRs and the workload consumes its connections from
``env``. This replaces the per-app Python adapters (``apptypes/``).
"""

from __future__ import annotations


def build_chart_values(
    *,
    client_slug: str,
    replicas: int,
    image: str,
    ingress_host: str,
    ingress_class_name: str,
    ingress_tls: list,
    ingress_annotations: dict | None,
    databases: list,
    sso: list,
    env: list,
    oidc: dict | None = None,
) -> dict:
    """Map the explicit intent onto the per-app chart's value contract (pure).

    Only generic, every-app fields are produced here; app-specific configuration
    (e.g. odoo workers) flows through the Application's free-form ``values`` and the
    chart's own defaults, never through controller code.

    ``oidc`` is the resolved OIDC descriptor (endpoints + client-id + the delivered
    Secret name) for the app's primary SSOApplication, injected as ``.Values.oidc`` so
    a chart can wire runtime SSO config (e.g. an operator ConfigMap) from values while
    still secretKeyRef'ing the client-secret. Omitted until the SSOApplication has
    published its client-id.
    """
    values: dict = {
        "platform": {"client": client_slug},
        "replicaCount": replicas,
        "databases": list(databases or []),
        "sso": list(sso or []),
        "env": list(env or []),
    }

    if oidc:
        values["oidc"] = dict(oidc)

    if image:
        repo, _, tag = image.partition(":")
        values["image"] = {"repository": repo, "tag": tag} if tag else {"repository": repo}

    if ingress_host:
        ingress: dict = {
            "enabled": True,
            "className": ingress_class_name,
            "hosts": [{"host": ingress_host, "paths": [{"path": "/", "pathType": "Prefix"}]}],
        }

        if ingress_tls:
            ingress["tls"] = ingress_tls

        if ingress_annotations:
            ingress["annotations"] = ingress_annotations

        values["ingress"] = ingress

    return values
