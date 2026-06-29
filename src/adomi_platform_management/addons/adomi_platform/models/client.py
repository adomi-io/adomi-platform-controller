from odoo import _, api, fields, models


class Client(models.Model):
    _name = "adomi.client"
    _description = "Adomi Client"
    _inherit = ["adomi.k8s.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "clients"
    _k8s_kind = "Client"

    name = fields.Char(required=True, tracking=True)
    slug = fields.Char(help="Stable identifier; defaults to the resource name.")
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        ondelete="restrict",
        tracking=True,
        help="The contact / company this platform client represents.",
    )
    organization_id = fields.Many2one("adomi.organization", string="Organization", ondelete="set null")
    workspace_ids = fields.One2many("adomi.workspace", "client_id", string="Workspaces")
    workspace_count = fields.Integer(compute="_compute_workspace_count")

    # Customer-centric rollup: every Application across all of the customer's
    # workspaces, plus an aggregated health signal for the kanban estate view.
    application_ids = fields.One2many("adomi.application", "client_id", string="Applications")
    application_count = fields.Integer(compute="_compute_app_stats")
    application_ready_count = fields.Integer(compute="_compute_app_stats")
    health = fields.Selection(
        [
            ("empty", "No apps"),
            ("ok", "Healthy"),
            ("pending", "Provisioning"),
            ("degraded", "Degraded"),
        ],
        compute="_compute_app_stats",
        string="Estate health",
        help="Aggregated readiness across all of the customer's applications.",
    )

    def _compute_workspace_count(self):
        for rec in self:
            rec.workspace_count = len(rec.workspace_ids)

    @api.depends("application_ids.k8s_state")
    def _compute_app_stats(self):
        for rec in self:
            apps = rec.application_ids
            rec.application_count = len(apps)
            rec.application_ready_count = len(apps.filtered(lambda a: a.k8s_state == "ready"))

            if not apps:
                rec.health = "empty"
            elif apps.filtered(lambda a: a.k8s_state == "not_ready"):
                rec.health = "degraded"
            elif apps.filtered(lambda a: a.k8s_state in ("pending", "unknown")):
                rec.health = "pending"
            else:
                rec.health = "ok"

    def _k8s_tenant_slug(self):
        return self.k8s_name or False

    def _k8s_spec(self):
        self.ensure_one()

        spec = {"displayName": self.name}

        if self.slug:
            spec["slug"] = self.slug

        if self.organization_id:
            spec["organizationRef"] = {"name": self.organization_id.k8s_name}

        return spec

    def _k8s_import_vals(self, obj):
        spec = obj.get("spec") or {}
        org_ref = (spec.get("organizationRef") or {}).get("name")
        org = (
            self.env["adomi.organization"].search([("k8s_name", "=", org_ref)], limit=1)
            if org_ref
            else self.env["adomi.organization"]
        )
        return {
            "name": spec.get("displayName") or (obj.get("metadata") or {}).get("name"),
            "slug": spec.get("slug") or False,
            "organization_id": org.id or False,
        }

    # --- customer-centric onboarding shortcuts ---
    def action_open_deploy_wizard(self):
        """Launch the guided deploy flow pre-scoped to this customer."""
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": _("Deploy Application"),
            "res_model": "adomi.deploy.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_client_id": self.id,
                "default_organization_id": self.organization_id.id or False,
            },
        }

    def action_view_applications(self):
        """Open this customer's applications (the estate)."""
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": _("%s · Applications") % self.name,
            "res_model": "adomi.application",
            "view_mode": "kanban,list,form",
            "domain": [("client_id", "=", self.id)],
            "context": {"default_client_id": self.id},
        }

    def action_view_workspaces(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": _("%s · Workspaces") % self.name,
            "res_model": "adomi.workspace",
            "view_mode": "list,form",
            "domain": [("client_id", "=", self.id)],
            "context": {"default_client_id": self.id},
        }
