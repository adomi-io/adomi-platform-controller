"""Seed/refresh the Authentik OIDC provider on upgrade.

post_init_hook only runs on a fresh install, so for databases where adomi_platform
is already installed this migration re-applies the env-driven OIDC setup on the
update that ships this version.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["adomi.oidc.setup"].setup_from_env()
