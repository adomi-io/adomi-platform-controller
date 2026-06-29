"""Guided onboarding: deploy an Application (creating Client / Workspace as needed).

One dialog instead of hand-creating four records. Pick or name a Client, pick or
name a Workspace, pick an Application Type, name the app — everything else defaults
from the type. Advanced overrides are tucked away. The wizard creates only the
records that don't exist yet and drops the user on the new Application.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models import k8s


class DeployWizard(models.TransientModel):
    _name = "adomi.deploy.wizard"
    _description = "Deploy Application wizard"

    organization_id = fields.Many2one("adomi.organization", string="Organization")

    # Client: pick existing or name a new one.
    client_id = fields.Many2one("adomi.client", string="Client")
    new_client_name = fields.Char(string="…or new client")
    new_client_partner_id = fields.Many2one("res.partner", string="Customer")

    # Workspace: pick existing (scoped to the client) or name a new one.
    workspace_id = fields.Many2one(
        "adomi.workspace",
        string="Workspace",
        domain="[('client_id', '=', client_id)]",
    )
    new_workspace_name = fields.Char(string="…or new workspace")
    new_workspace_class = fields.Selection(
        [
            ("production", "Production"),
            ("development", "Development"),
            ("pdi", "PDI"),
            ("preview", "Preview"),
            ("test", "Test"),
        ],
        string="Workspace class",
        default="development",
    )

    type_id = fields.Many2one("adomi.application.type", string="Application type", required=True)
    app_name = fields.Char(string="Application name", required=True)
    hostname = fields.Char(string="Hostname", help="Override the generated ingress host.")

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)

        # Pre-select the only organization, if there is exactly one.
        orgs = self.env["adomi.organization"].search([])

        if len(orgs) == 1:
            vals.setdefault("organization_id", orgs.id)

        return vals

    @api.onchange("client_id")
    def _onchange_client_id(self):
        if self.client_id:
            self.new_client_name = False

            if self.client_id.organization_id:
                self.organization_id = self.client_id.organization_id

            if self.workspace_id and self.workspace_id.client_id != self.client_id:
                self.workspace_id = False

    @api.onchange("workspace_id")
    def _onchange_workspace_id(self):
        if self.workspace_id:
            self.new_workspace_name = False

    def _resolve_client(self):
        if self.client_id:
            return self.client_id

        if not self.new_client_name:
            raise UserError(_("Pick an existing client or enter a new client name."))

        return self.env["adomi.client"].create(
            {
                "name": self.new_client_name,
                "k8s_name": k8s.slugify(self.new_client_name),
                "organization_id": self.organization_id.id or False,
                "partner_id": self.new_client_partner_id.id or False,
            }
        )

    def _resolve_workspace(self, client):
        if self.workspace_id:
            return self.workspace_id

        if not self.new_workspace_name:
            raise UserError(_("Pick an existing workspace or enter a new workspace name."))

        return self.env["adomi.workspace"].create(
            {
                "name": self.new_workspace_name,
                "k8s_name": k8s.slugify(self.new_workspace_name),
                "client_id": client.id,
                "workspace_class": self.new_workspace_class or "development",
            }
        )

    def action_deploy(self):
        self.ensure_one()

        if not self.app_name:
            raise UserError(_("Give the application a name."))

        client = self._resolve_client()
        workspace = self._resolve_workspace(client)
        application = self.env["adomi.application"].create(
            {
                "name": self.app_name,
                "k8s_name": k8s.slugify(self.app_name),
                "workspace_id": workspace.id,
                "type_id": self.type_id.id,
                "hostname": self.hostname or False,
            }
        )

        return {
            "type": "ir.actions.act_window",
            "name": _("Application"),
            "res_model": "adomi.application",
            "res_id": application.id,
            "view_mode": "form",
            "target": "current",
        }
