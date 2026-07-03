from odoo import fields, models


class Workspace(models.Model):
    _name = "adomi.workspace"
    _description = "Adomi Workspace"
    _inherit = ["adomi.k8s.mixin", "adomi.observability.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "workspaces"
    _k8s_kind = "Workspace"

    name = fields.Char(required=True, tracking=True)
    client_id = fields.Many2one("adomi.client", string="Client", required=True, ondelete="cascade")
    workspace_class = fields.Selection(
        [
            ("production", "Production"),
            ("development", "Development"),
            ("pdi", "PDI"),
            ("preview", "Preview"),
            ("test", "Test"),
        ],
        string="Class",
        default="development",
        required=True,
        tracking=True,
    )
    namespace = fields.Char(string="Namespace", readonly=True, copy=False)
    application_ids = fields.One2many("adomi.application", "workspace_id", string="Applications")

    def _k8s_tenant_slug(self):
        return self.client_id.k8s_name or False

    def _api_body(self):
        self.ensure_one()

        return {
            "display_name": self.name,
            "class": self.workspace_class,
        }

    def _k8s_spec(self):
        self.ensure_one()

        return {
            "clientRef": {"name": self.client_id.k8s_name},
            "class": self.workspace_class,
            "displayName": self.name,
        }

    def _k8s_status_vals(self, obj):
        return {
            "namespace": (obj.get("status") or {}).get("namespace") or False,
        }

    def _k8s_import_vals(self, obj):
        spec = obj.get("spec") or {}
        client_ref = (spec.get("clientRef") or {}).get("name")
        client = (
            self.env["adomi.client"].search([("k8s_name", "=", client_ref)], limit=1)
            if client_ref
            else self.env["adomi.client"]
        )
        if not client:
            return None  # the customer must be imported first
        return {
            "name": spec.get("displayName") or (obj.get("metadata") or {}).get("name"),
            "client_id": client.id,
            "workspace_class": spec.get("class") or "development",
        }
