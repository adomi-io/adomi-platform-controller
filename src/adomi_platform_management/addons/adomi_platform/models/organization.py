from odoo import fields, models


class Organization(models.Model):
    _name = "adomi.organization"
    _description = "Adomi Organization"
    _inherit = ["adomi.k8s.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "organizations"
    _k8s_kind = "Organization"
    _k8s_cluster_scoped = True

    name = fields.Char(required=True, tracking=True)
    base_domain = fields.Char(
        tracking=True, help="Base domain for generated hostnames (<app>.<environment>.<client>.<base>)."
    )
    odoo_image_repository = fields.Char(help="Default Odoo image repository.")
    ingress_class = fields.Char(default="traefik")
    client_ids = fields.One2many("adomi.client", "organization_id", string="Clients")

    def _k8s_spec(self):
        self.ensure_one()

        spec = {}

        if self.base_domain:
            spec["domain"] = {"base": self.base_domain}

        if self.odoo_image_repository:
            spec["images"] = {"odooRepository": self.odoo_image_repository}

        if self.ingress_class:
            spec["ingress"] = {"className": self.ingress_class}

        return spec

    def _k8s_import_vals(self, obj):
        spec = obj.get("spec") or {}
        return {
            "name": (obj.get("metadata") or {}).get("name"),
            "base_domain": (spec.get("domain") or {}).get("base") or False,
            "odoo_image_repository": (spec.get("images") or {}).get("odooRepository") or False,
            "ingress_class": (spec.get("ingress") or {}).get("className") or "traefik",
        }
