"""Re-seed the Authentik OIDC provider on upgrade to this version.

This version adds the `groups` scope to the provider so the id_token carries the
Authentik group claim (required for the res_users role sync to grant admin to
"Platform Admins"). post_init_hook only runs on a fresh install, so re-apply the
env-driven setup here for databases where adomi_platform is already installed.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["adomi.oidc.setup"].setup_from_env()
