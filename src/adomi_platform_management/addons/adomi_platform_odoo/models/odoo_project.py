"""An Odoo project: one customer Odoo deployment and the code behind it.

The launch wizard creates one per Odoo application it deploys, so the product
has a first-class record to hang its experience on — the edition, the pipeline
repository, the deployment — instead of those living only as loose application
fields. The upcoming product dashboard (task #14) lists and manages these.
"""

from odoo import _, fields, models


class OdooProject(models.Model):
    _name = "adomi.odoo.project"
    _description = "Odoo Project"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(required=True, tracking=True)
    application_id = fields.Many2one(
        "adomi.application",
        string="Application",
        required=True,
        ondelete="cascade",
        index=True,
        domain="[('type_id.k8s_name', '=', 'odoo')]",
    )
    client_id = fields.Many2one(
        related="application_id.client_id", string="Customer", store=True, readonly=True
    )
    environment_id = fields.Many2one(
        related="application_id.environment_id", string="Environment", readonly=True
    )
    edition = fields.Selection(
        [("community", "Community"), ("enterprise", "Enterprise")],
        default="community",
        required=True,
        tracking=True,
    )
    git_repository_id = fields.Many2one(
        related="application_id.git_repository_id", string="Repository", readonly=True
    )
    repo_url = fields.Char(related="git_repository_id.url", string="Repository URL")
    pipeline_synced_at = fields.Datetime(
        related="application_id.odoo_pipeline_synced_at", string="Pipeline committed"
    )
    k8s_state = fields.Selection(related="application_id.k8s_state", string="Status")
    url = fields.Char(related="application_id.url", string="URL")

    _sql_constraints = [
        (
            "application_unique",
            "unique(application_id)",
            "This application already has an Odoo project.",
        ),
    ]

    def action_open_application(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": _("Application"),
            "res_model": "adomi.application",
            "res_id": self.application_id.id,
            "view_mode": "form",
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_open_repo(self):
        self.ensure_one()

        if not self.repo_url:
            return False

        return {"type": "ir.actions.act_url", "url": self.repo_url, "target": "new"}
