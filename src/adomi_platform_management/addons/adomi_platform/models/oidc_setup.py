"""Seed the Authentik OIDC provider from the environment.

The platform controller reconciles the portal's SSOApplication into an Authentik
OAuth2 provider and publishes the client credentials as the ``management-sso``
Secret, which the deployment exposes as env vars. This model turns those into an
``auth.oauth.provider`` record (OCA ``auth_oidc``) so users can sign in to the portal
with Authentik — no manual Authentik/Odoo clicking.

Run on install (post_init_hook) and on every module update via data/oidc_setup.xml
(a non-noupdate <function> record; the deployment runs ``-u adomi_platform`` each
boot), so every pod start re-syncs the provider from the environment. Idempotent:
it upserts a single provider via a stable xml id.

Env (set by the kubernetes-provisioner):
  ADOMI_OIDC_CLIENT_ID / ADOMI_OIDC_CLIENT_SECRET  - from the management-sso Secret
  ADOMI_OIDC_AUTH_HOST                             - e.g. auth.example.com
  ADOMI_OIDC_APP_SLUG                              - Authentik application slug
  ADOMI_PORTAL_BASE_URL                            - e.g. https://platform.example.com
"""

import logging
import os

from odoo import api, models

_logger = logging.getLogger(__name__)

PROVIDER_XMLID = "adomi_platform.auth_oauth_provider_authentik"


class OidcSetup(models.AbstractModel):
    _name = "adomi.oidc.setup"
    _description = "Adomi OIDC provider setup (from environment)"

    @api.model
    def setup_from_env(self):
        """Create/update the Authentik auth.oauth.provider from env vars."""
        icp = self.env["ir.config_parameter"].sudo()

        base_url = (os.environ.get("ADOMI_PORTAL_BASE_URL") or "").strip()

        if base_url:
            # Odoo builds the OAuth redirect_uri from web.base.url; it must match the
            # SSOApplication redirectUris or Authentik rejects the callback. Freeze it
            # so a later admin login doesn't overwrite it.
            icp.set_param("web.base.url", base_url)
            icp.set_param("web.base.url.freeze", "True")

        client_id = (os.environ.get("ADOMI_OIDC_CLIENT_ID") or "").strip()
        client_secret = (os.environ.get("ADOMI_OIDC_CLIENT_SECRET") or "").strip()
        auth_host = (os.environ.get("ADOMI_OIDC_AUTH_HOST") or "").strip()
        slug = (os.environ.get("ADOMI_OIDC_APP_SLUG") or "adomi-platform-management").strip()

        if not (client_id and auth_host):
            _logger.info(
                "Adomi OIDC: ADOMI_OIDC_CLIENT_ID / ADOMI_OIDC_AUTH_HOST not set; "
                "skipping provider setup (SSO not reconciled yet?)."
            )

            return False

        base = "https://%s/application/o" % auth_host

        vals = {
            "name": "Authentik",
            "flow": "id_token_code",  # OpenID Connect authorization code flow
            # `groups` is required: Authentik only puts the group claim in the
            # id_token when the scope is requested, and the role sync (res_users)
            # reads it to grant admin to "Platform Admins". Mirrors ArgoCD's scopes.
            "scope": "openid profile email groups",
            "auth_endpoint": "%s/authorize/" % base,
            "token_endpoint": "%s/token/" % base,
            "validation_endpoint": "%s/userinfo/" % base,
            "jwks_uri": "%s/%s/jwks/" % (base, slug),
            "client_id": client_id,
            "client_secret": client_secret,
            "enabled": True,
            "css_class": "fa fa-fw fa-shield",
            "body": "Log in with Authentik",
        }

        provider = self.env.ref(PROVIDER_XMLID, raise_if_not_found=False)

        if provider:
            provider.sudo().write(vals)
        else:
            provider = self.env["auth.oauth.provider"].sudo().create(vals)
            module, name = PROVIDER_XMLID.split(".", 1)
            self.env["ir.model.data"].sudo().create(
                {
                    "name": name,
                    "module": module,
                    "model": "auth.oauth.provider",
                    "res_id": provider.id,
                    "noupdate": True,
                }
            )

        _logger.info("Adomi OIDC: Authentik provider configured (slug=%s).", slug)

        return True
