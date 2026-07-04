from odoo import fields, models


class Environment(models.Model):
    _name = "adomi.environment"
    _description = "Adomi Environment"
    _inherit = ["adomi.k8s.mixin", "adomi.observability.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "environments"
    _k8s_kind = "Environment"

    name = fields.Char(required=True, tracking=True)
    client_id = fields.Many2one("adomi.client", string="Client", required=True, ondelete="cascade")
    environment_class = fields.Selection(
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
    application_ids = fields.One2many("adomi.application", "environment_id", string="Applications")

    def _k8s_client_slug(self):
        return self.client_id.k8s_name or False

    def _api_body(self):
        self.ensure_one()

        return {
            "display_name": self.name,
            "class": self.environment_class,
        }

    def _k8s_spec(self):
        self.ensure_one()

        return {
            "clientRef": {"name": self.client_id.k8s_name},
            "class": self.environment_class,
            "displayName": self.name,
        }

    def _k8s_status_vals(self, obj):
        return {
            "namespace": (obj.get("status") or {}).get("namespace") or False,
        }

    def _k8s_identity_domain(self, obj):
        # "production" exists in every client: identity is (client, name).
        domain = super()._k8s_identity_domain(obj)
        slug = ((obj.get("spec") or {}).get("clientRef") or {}).get(
            "name"
        ) or self._k8s_obj_client_slug(obj)
        if slug:
            domain.append(("client_id.k8s_name", "=", slug))
        return domain

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
            "environment_class": spec.get("class") or "development",
        }
