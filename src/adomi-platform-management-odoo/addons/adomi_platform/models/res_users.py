"""Map Authentik groups to Odoo roles on OIDC sign-in.

When a user signs in through Authentik (OCA auth_oidc), the id_token carries a
``groups`` claim (the SSOApplication requests the ``groups`` scope). On each login we
sync the user's Odoo access from that claim, so access levels are driven by Authentik
group membership instead of manual per-user setup:

* every successful OIDC user is an internal user (``base.group_user``);
* membership of the admin group (``adomi_platform.oidc_admin_group``, default
  "Platform Admins") grants/revokes Settings access (``base.group_system``).

The bootstrap admin (``base.user_admin``) and the superuser are never touched, so a
local break-glass login always remains.
"""

import logging

from odoo import SUPERUSER_ID, api, models

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _auth_oauth_signin(self, provider, validation, params):
        login = super()._auth_oauth_signin(provider, validation, params)

        try:
            self._adomi_sync_oidc_groups(login, validation)
        except Exception:  # noqa: BLE001 - never block login on a mapping error
            _logger.exception("Adomi: OIDC group sync failed for %s", login)

        return login

    def _adomi_sync_oidc_groups(self, login, validation):
        if not login:
            return

        user = self.sudo().search([("login", "=", login)], limit=1)

        if not user or user.id == SUPERUSER_ID:
            return

        admin_user = self.env.ref("base.user_admin", raise_if_not_found=False)

        if admin_user and user.id == admin_user.id:
            return  # keep the break-glass admin untouched

        claim = validation.get("groups") or []

        if isinstance(claim, str):
            claim = [claim]

        admin_group_name = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("adomi_platform.oidc_admin_group", "Platform Admins")
        )

        add = self.env["res.groups"]
        remove = self.env["res.groups"]
        internal = self.env.ref("base.group_user", raise_if_not_found=False)

        if internal:
            add |= internal

        system = self.env.ref("base.group_system", raise_if_not_found=False)

        if system:
            if admin_group_name in claim:
                add |= system
            else:
                remove |= system

        commands = [(4, g.id) for g in add] + [(3, g.id) for g in remove]

        if commands:
            user.sudo().write({"groups_id": commands})

        _logger.info(
            "Adomi: synced OIDC roles for %s (admin=%s)", login, admin_group_name in claim
        )
