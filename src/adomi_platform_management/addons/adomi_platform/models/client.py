import os

from odoo import _, api, fields, models


class Client(models.Model):
    _name = "adomi.client"
    _description = "Adomi Client"
    _inherit = ["adomi.k8s.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "clients"
    _k8s_kind = "Client"

    name = fields.Char(required=True, tracking=True)
    scoped_config_ids = fields.One2many(
        "adomi.scoped.config", "client_id", string="Variables & Secrets"
    )
    slug = fields.Char(help="Stable identifier; defaults to the resource name.")
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        ondelete="restrict",
        tracking=True,
        help="The contact / company this platform client represents.",
    )
    organization_id = fields.Many2one("adomi.organization", string="Organization", ondelete="set null")
    environment_ids = fields.One2many("adomi.environment", "client_id", string="Environments")
    environment_count = fields.Integer(compute="_compute_environment_count")

    # Customer-centric rollup: every Application across all of the customer's
    # environments, plus an aggregated health signal for the kanban estate view.
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

    # --- infrastructure repository (the visible face of the GitOps flow) ---
    # Every customer gets an infrastructure repo holding their committed intent;
    # the form shows it as a provisioning journey (committed -> applied -> ready)
    # with a deep link, so a non-technical user SEES what creating a customer did.
    infra_repo_url = fields.Char(compute="_compute_infra_repo", string="Infrastructure repository")
    provisioning_stage = fields.Selection(
        [
            ("committed", "Committed to repository"),
            ("applied", "Applied to the platform"),
            ("ready", "Ready"),
            ("failed", "Attention needed"),
        ],
        compute="_compute_infra_repo",
        string="Provisioning",
    )

    @api.model
    def _git_public_base(self):
        """Public web URL of the org holding customer infra repos (deep links)."""
        return (
            os.environ.get("ADOMI_GIT_PUBLIC_BASE")
            or self.env["ir.config_parameter"].sudo().get_param("adomi_platform.git_public_base")
            or ""
        ).rstrip("/")

    @api.depends("k8s_name", "k8s_state", "k8s_message")
    def _compute_infra_repo(self):
        base = self._git_public_base()
        for rec in self:
            rec.infra_repo_url = "%s/%s" % (base, rec.k8s_name) if base and rec.k8s_name else False
            message = (rec.k8s_message or "").lower()
            if rec.k8s_state == "ready":
                rec.provisioning_stage = "ready"
            elif rec.k8s_state == "not_ready" or "failed" in message:
                rec.provisioning_stage = "failed"
            elif rec.k8s_state == "pending" and (not message or message.startswith("committed")):
                # In git (or just pushed), GitOps hasn't applied it to the cluster yet.
                rec.provisioning_stage = "committed"
            else:
                # The CR exists in the cluster; the controller is reconciling.
                rec.provisioning_stage = "applied"

    def _compute_environment_count(self):
        for rec in self:
            rec.environment_count = len(rec.environment_ids)

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

    def _k8s_client_slug(self):
        return self.k8s_name or False

    def _api_path(self):
        # The client itself is the resource: PUT /v1/clients/{client}.
        self.ensure_one()
        return "/v1/clients/%s" % self.k8s_name

    def _api_body(self):
        self.ensure_one()

        body = {"display_name": self.name}

        if self.slug:
            body["slug"] = self.slug

        if self.organization_id:
            body["organization"] = self.organization_id.k8s_name

        return body

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

    domain_ids = fields.One2many("adomi.domain", "client_id", string="Domains")
    database_server_ids = fields.One2many(
        "adomi.database.server", "client_id", string="Database servers"
    )

    # --- the portal payload: everything the customer page renders, one call ---
    def get_portal_data(self):
        """Everything the customer-portal view shows, in one round trip.

        The page is a read of the whole estate (domains, database servers, the
        environment -> application tree with hosts / databases / variables);
        every mutation goes through the regular records + dialogs, then the
        widget reloads this.
        """
        self.ensure_one()

        env_class_order = {"production": 0, "pdi": 1, "test": 2, "development": 3, "preview": 4}

        domains = [
            {
                "id": d.id,
                "fqdn": d.fqdn,
                "mode": d.mode,
                "wildcard": d.wildcard,
                "state": d.k8s_state,
                "message": d.k8s_message or "",
                "cname_target": d.cname_target or "",
                "app_count": len(d.application_ids),
            }
            for d in self.domain_ids
        ]

        def server_data(s):
            return {
                "id": s.id,
                "name": s.name,
                "mode": s.mode,
                "engine": s.engine,
                "storage": s.cnpg_storage or "",
                "instances": s.cnpg_instances,
                "external_host": s.external_host or "",
                "external_port": s.external_port,
                "host": s.host or "",
                "environment": s.environment_id.name or "",
                "state": s.k8s_state,
            }

        def app_data(a):
            return {
                "id": a.id,
                "name": a.name,
                "k8s_name": a.k8s_name,
                "type": a.type_id.name or a.type_id.k8s_name,
                "state": a.k8s_state,
                "message": a.k8s_message or "",
                "phase": a.phase or "",
                "url": a.url or "",
                "host": {
                    "subdomain": a.subdomain or "",
                    "domain": a.domain_id.fqdn or "",
                    "hostname": a.hostname or "",
                    "effective": a.host_effective or "",
                },
                "databases": [
                    {
                        "id": d.id,
                        "name": d.name,
                        "server": d.server_id.name or d.server_name or "",
                        "server_mode": d.server_id.mode or "",
                        "database_name": d.database_name or d.name,
                        "secret": d.secret,
                    }
                    for d in a.database_ids
                ],
                "config": a.get_effective_config(),
            }

        environments = [
            {
                "id": e.id,
                "name": e.name,
                "environment_class": e.environment_class,
                "namespace": e.namespace or "",
                "state": e.k8s_state,
                "message": e.k8s_message or "",
                "servers": [
                    server_data(s)
                    for s in self.database_server_ids
                    if s.environment_id == e or not s.environment_id
                ],
                "apps": [app_data(a) for a in e.application_ids],
            }
            for e in self.environment_ids.sorted(
                key=lambda e: (env_class_order.get(e.environment_class, 9), e.name)
            )
        ]

        return {
            "client": {
                "id": self.id,
                "name": self.name,
                "slug": self.k8s_name,
                "organization": self.organization_id.name or "",
                "partner": self.partner_id.display_name or "",
                "health": self.health,
                "state": self.k8s_state,
                "message": self.k8s_message or "",
                "application_count": self.application_count,
                "application_ready_count": self.application_ready_count,
                "environment_count": self.environment_count,
                "provisioning_stage": self.provisioning_stage,
                "infra_repo_url": self.infra_repo_url or "",
                "base_domain": self.organization_id.base_domain or "",
            },
            "domains": domains,
            "servers": [server_data(s) for s in self.database_server_ids],
            "environments": environments,
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

    def action_view_environments(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": _("%s · Environments") % self.name,
            "res_model": "adomi.environment",
            "view_mode": "list,form",
            "domain": [("client_id", "=", self.id)],
            "context": {"default_client_id": self.id},
        }
