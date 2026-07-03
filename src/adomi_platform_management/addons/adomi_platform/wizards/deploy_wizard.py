"""Guided onboarding: deploy an Application (creating Client / Environment as needed).

One dialog instead of hand-creating four records. Pick or name a Client, pick or
name a Environment, pick an Application Type, name the app — everything else defaults
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

    # Environment: pick existing (scoped to the client) or name a new one.
    environment_id = fields.Many2one(
        "adomi.environment",
        string="Environment",
        domain="[('client_id', '=', client_id)]",
    )
    new_environment_name = fields.Char(string="…or new environment")
    new_environment_class = fields.Selection(
        [
            ("production", "Production"),
            ("development", "Development"),
            ("pdi", "PDI"),
            ("preview", "Preview"),
            ("test", "Test"),
        ],
        string="Environment class",
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

            if self.environment_id and self.environment_id.client_id != self.client_id:
                self.environment_id = False

    @api.onchange("environment_id")
    def _onchange_environment_id(self):
        if self.environment_id:
            self.new_environment_name = False

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

    def _resolve_environment(self, client):
        if self.environment_id:
            return self.environment_id

        if not self.new_environment_name:
            raise UserError(_("Pick an existing environment or enter a new environment name."))

        return self.env["adomi.environment"].create(
            {
                "name": self.new_environment_name,
                "k8s_name": k8s.slugify(self.new_environment_name),
                "client_id": client.id,
                "environment_class": self.new_environment_class or "development",
            }
        )

    def action_deploy(self):
        self.ensure_one()

        if not self.app_name:
            raise UserError(_("Give the application a name."))

        client = self._resolve_client()
        environment = self._resolve_environment(client)
        application = self.env["adomi.application"].create(
            {
                "name": self.app_name,
                "k8s_name": k8s.slugify(self.app_name),
                "environment_id": environment.id,
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
