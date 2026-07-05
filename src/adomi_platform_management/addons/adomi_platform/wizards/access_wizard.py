import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AppAccessWizard(models.TransientModel):
    """Grant one Authentik user access to one application.

    Granting the FIRST user flips the app from "everyone with SSO" to
    members-only (the platform binds the app's access group in Authentik);
    revoking the last one opens it up again. The user picker reads from the
    mirrored Authentik directory, refreshed when the dialog opens.
    """

    _name = "adomi.app.access.wizard"
    _description = "Grant application access"

    application_id = fields.Many2one(
        "adomi.application", string="Application", required=True, ondelete="cascade"
    )
    user_id = fields.Many2one("adomi.authentik.user", string="User", required=True)

    @api.model
    def default_get(self, fields_list):
        # Refresh the directory so the picker is current; a directory that is
        # briefly stale must not block granting access.
        try:
            self.env["adomi.authentik.user"].sync_from_platform()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Authentik directory sync failed: %s", exc)
        return super().default_get(fields_list)

    def action_grant(self):
        self.ensure_one()

        from ..models.api_client import PlatformApiError

        app = self.application_id
        try:
            app._platform_api().upsert(
                app._api_path() + "/access/%s" % self.user_id.authentik_pk, {}
            )
        except PlatformApiError as exc:
            raise UserError(
                _("Granting access failed: %s") % exc
            ) from exc

        return {"type": "ir.actions.act_window_close"}
