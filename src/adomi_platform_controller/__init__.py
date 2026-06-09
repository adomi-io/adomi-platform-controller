"""Adomi platform controller.

A Kubernetes operator that reconciles a single CRD, `SSOApplication`, into state
across three backends, with OpenBao as the source of truth for credentials:

  * SSOApplication -> OpenBao credentials (generate-once), an Authentik OAuth2
                      provider + application, and an ExternalSecret that publishes
                      the credentials into the app's namespace

It is built on Kopf and drops into the kubernetes-provisioner platform.
"""

__version__ = "0.1.0"