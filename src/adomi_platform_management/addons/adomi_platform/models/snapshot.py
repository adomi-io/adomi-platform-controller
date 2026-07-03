from odoo import fields, models


class Snapshot(models.Model):
    _name = "adomi.snapshot"
    _description = "Adomi Snapshot"
    _inherit = ["adomi.k8s.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "snapshots"
    _k8s_kind = "Snapshot"

    name = fields.Char(required=True, tracking=True)
    application_id = fields.Many2one(
        "adomi.application", string="Application", required=True, ondelete="cascade"
    )
    location = fields.Char(string="Location", readonly=True, help="Object-store URI of the dump.")
    phase = fields.Char(string="Phase", readonly=True)

    def _k8s_tenant_slug(self):
        return self.application_id.client_id.k8s_name or False

    def _api_body(self):
        self.ensure_one()

        return {"application": self.application_id.k8s_name}

    def _k8s_spec(self):
        self.ensure_one()

        return {
            "applicationRef": {"name": self.application_id.k8s_name},
        }

    def _k8s_status_vals(self, obj):
        status = obj.get("status") or {}

        return {
            "location": status.get("location") or False,
            "phase": status.get("phase") or False,
        }
