from odoo import api, fields, models

from . import k8s


class ApplicationType(models.Model):
    _name = "adomi.application.type"
    _description = "Adomi Application Type (catalog)"
    _inherit = ["adomi.k8s.mixin"]
    _rec_name = "name"

    _k8s_plural = "applicationtypes"
    _k8s_kind = "ApplicationType"
    _k8s_cluster_scoped = True

    name = fields.Char(required=True)
    adapter = fields.Char(help="Controller adapter: odoo, superset, mailpit, generic.")
    chart_repo_url = fields.Char(string="Chart repo URL")
    chart_name = fields.Char(string="Chart (Helm repo)")
    chart_path = fields.Char(string="Chart path (git)")
    chart_target_revision = fields.Char(string="Chart revision")
    database_required = fields.Boolean(string="Needs database")
    sso_protocol = fields.Selection(
        [("oauth2", "OAuth2 / OIDC"), ("proxy", "Forward-auth proxy")], string="SSO"
    )
    provides = fields.Char(help="Connection capabilities (url, db, smtp, oidc).")

    def _k8s_spec(self):
        self.ensure_one()

        chart = {}

        if self.chart_repo_url:
            chart["repoURL"] = self.chart_repo_url

        if self.chart_name:
            chart["chart"] = self.chart_name

        if self.chart_path:
            chart["path"] = self.chart_path

        if self.chart_target_revision:
            chart["targetRevision"] = self.chart_target_revision

        spec = {
            "displayName": self.name,
            "adapter": self.adapter or "generic",
            "chart": chart,
            "database": {"required": self.database_required},
        }

        if self.sso_protocol:
            spec["sso"] = {"enabled": True, "protocol": self.sso_protocol}

        if self.provides:
            spec["provides"] = [p.strip() for p in self.provides.split(",") if p.strip()]

        return spec

    @api.model
    def action_import_types(self):
        """Pull the cluster's ApplicationType catalog into Odoo (idempotent)."""
        for obj in k8s.list_("applicationtypes"):
            name = obj["metadata"]["name"]
            spec = obj.get("spec") or {}
            chart = spec.get("chart") or {}

            vals = {
                "name": spec.get("displayName") or name,
                "k8s_name": name,
                "adapter": spec.get("adapter") or "generic",
                "chart_repo_url": chart.get("repoURL") or False,
                "chart_name": chart.get("chart") or False,
                "chart_path": chart.get("path") or False,
                "chart_target_revision": chart.get("targetRevision") or False,
                "database_required": bool((spec.get("database") or {}).get("required")),
                "sso_protocol": (spec.get("sso") or {}).get("protocol") or False,
                "provides": ",".join(spec.get("provides") or []) or False,
            }

            rec = self.with_context(adomi_no_push=True).search([("k8s_name", "=", name)], limit=1)

            if rec:
                rec.with_context(adomi_no_push=True).write(vals)
            else:
                self.with_context(adomi_no_push=True).create(vals)

        return True
