from odoo import api, fields, models

from . import k8s


class Domain(models.Model):
    """A DNS domain a customer's apps are published under.

    Two ways in: the customer brings their own domain (they create a CNAME
    pointing at the platform edge), or they run on a name under the
    organization's base domain (nothing to do on their side). Applications
    pick a domain + subdomain to build their hostname.
    """

    _name = "adomi.domain"
    _description = "Adomi Domain"
    _inherit = ["adomi.k8s.mixin", "mail.thread"]
    _rec_name = "fqdn"

    _k8s_plural = "domains"
    _k8s_kind = "Domain"

    name = fields.Char(
        required=True,
        tracking=True,
        help="Display name; defaults to the domain itself.",
    )
    client_id = fields.Many2one(
        "adomi.client", string="Customer", required=True, ondelete="cascade", index=True
    )
    mode = fields.Selection(
        [
            ("byo", "They bring their own domain"),
            ("platform", "Run on our domain"),
        ],
        string="Kind",
        default="byo",
        required=True,
        tracking=True,
        help="byo: the customer owns the DNS zone and creates a CNAME to the "
        "platform edge. platform: a name under the organization's base domain, "
        "DNS and certificates fully managed by the platform.",
    )
    fqdn = fields.Char(
        string="Domain",
        required=True,
        tracking=True,
        help="The fully-qualified domain apps are published under, e.g. acme.com.",
    )
    platform_label = fields.Char(
        string="Subdomain",
        help="Label under the organization's base domain (platform mode): "
        "<label>.<base domain>.",
    )
    wildcard = fields.Boolean(
        string="Wildcard certificate",
        default=True,
        help="Issue a wildcard cert (*.domain) instead of per-host certificates.",
    )
    issuer = fields.Char(
        string="Certificate issuer",
        help="cert-manager ClusterIssuer override (advanced; empty = platform default).",
    )
    cname_target = fields.Char(
        compute="_compute_cname_target",
        string="CNAME target",
        help="Where the customer points their DNS record (the platform edge).",
    )
    base_domain = fields.Char(
        compute="_compute_base_domain",
        help="The domain platform-managed names are generated under; the view "
        "uses it to know whether 'Run on our domain' can derive the result.",
    )
    host = fields.Char(string="Resolved host", readonly=True, copy=False)
    application_ids = fields.One2many("adomi.application", "domain_id", string="Applications")

    _sql_constraints = [
        (
            "fqdn_per_client_unique",
            "unique(client_id, fqdn)",
            "This customer already has that domain.",
        ),
    ]

    @api.model
    def _edge_host(self):
        """The host BYO domains CNAME to (the platform's ingress edge)."""
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param("adomi_platform.edge_host") or "").strip()

    def _org_base_domain(self):
        """The domain to generate platform-managed names under.

        The customer's organization wins; a platform-level parameter backs it
        so customers without an organization still get 'Run on our domain'.
        """
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        return (
            self.client_id.organization_id.base_domain
            or icp.get_param("adomi_platform.base_domain")
            or ""
        ).strip()

    @api.depends("client_id.organization_id.base_domain")
    def _compute_base_domain(self):
        for rec in self:
            rec.base_domain = rec._org_base_domain() or False

    @api.depends("mode", "client_id.organization_id.base_domain")
    def _compute_cname_target(self):
        edge = self._edge_host()
        for rec in self:
            # Fall back to the org base domain: its wildcard record already
            # resolves to the same ingress the edge host would.
            rec.cname_target = edge or rec._org_base_domain() or False

    @api.onchange("mode", "platform_label", "client_id")
    def _onchange_platform_fqdn(self):
        for rec in self:
            if rec.mode == "platform":
                base = rec._org_base_domain()
                label = k8s.slugify(rec.platform_label or rec.client_id.k8s_name or "")
                if base and label and label != "item":
                    rec.fqdn = "%s.%s" % (label, base)

    @api.onchange("fqdn")
    def _onchange_fqdn_name(self):
        for rec in self:
            if rec.fqdn and not rec.name:
                rec.name = rec.fqdn
            if rec.fqdn and not rec.k8s_name:
                rec.k8s_name = k8s.slugify(rec.fqdn)

    @api.model_create_multi
    def create(self, vals_list):
        # The mixin derives k8s_name from name; a domain's natural identity is
        # its fqdn, so default both from it.
        for vals in vals_list:
            if vals.get("fqdn"):
                vals.setdefault("name", vals["fqdn"])
                if not vals.get("k8s_name"):
                    vals["k8s_name"] = k8s.slugify(vals["fqdn"])
        return super().create(vals_list)

    def action_remove(self):
        """Delete from a dialog footer (unlink + close, like adomi.scoped.config)."""
        self.unlink()
        return {"type": "ir.actions.act_window_close"}

    def _k8s_client_slug(self):
        return self.client_id.k8s_name or False

    def _api_body(self):
        self.ensure_one()

        body = {"fqdn": (self.fqdn or "").strip().lower(), "wildcard": self.wildcard}

        if self.issuer:
            body["issuer"] = self.issuer

        return body

    def _k8s_spec(self):
        self.ensure_one()

        spec = {"fqdn": (self.fqdn or "").strip().lower(), "wildcard": self.wildcard}

        if self.issuer:
            spec["issuer"] = self.issuer

        return spec

    def _k8s_status_vals(self, obj):
        return {
            "host": (obj.get("status") or {}).get("host") or False,
        }

    def _k8s_identity_domain(self, obj):
        # The same domain resource name may exist in every client: identity is
        # (client, name).
        domain = super()._k8s_identity_domain(obj)
        slug = self._k8s_obj_client_slug(obj)
        if slug:
            domain.append(("client_id.k8s_name", "=", slug))
        return domain

    def _k8s_import_vals(self, obj):
        spec = obj.get("spec") or {}
        slug = self._k8s_obj_client_slug(obj)
        client = (
            self.env["adomi.client"].search([("k8s_name", "=", slug)], limit=1)
            if slug
            else self.env["adomi.client"]
        )
        if not client:
            return None  # the customer must be imported first

        fqdn = (spec.get("fqdn") or "").strip().lower()
        icp = self.env["ir.config_parameter"].sudo()
        base = (
            client.organization_id.base_domain or icp.get_param("adomi_platform.base_domain") or ""
        ).strip().lower()
        mode = "platform" if base and fqdn.endswith("." + base) else "byo"

        vals = {
            "name": fqdn or (obj.get("metadata") or {}).get("name"),
            "client_id": client.id,
            "fqdn": fqdn,
            "mode": mode,
            "wildcard": spec.get("wildcard", True),
            "issuer": spec.get("issuer") or False,
        }
        if mode == "platform":
            vals["platform_label"] = fqdn[: -len(base) - 1]
        return vals
