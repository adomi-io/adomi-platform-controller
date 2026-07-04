"""Guided onboarding: launch an Application step by step.

The CORE launch flow: pick/create the customer and environment, choose an app
from the catalog, wire its database, add variables/secrets, review, launch.
Product addons (adomi_platform_odoo, ...) EXTEND this wizard — injecting their
own steps, contributing application values, and doing post-launch work (e.g.
generating a boilerplate repo) — so every product launches through one familiar
flow. Extension points:

- ``_wizard_steps()``       ordered (key, label) steps; addons insert their own.
- ``_step_visible(step)``   hide steps that don't apply to the chosen type.
- ``_prepare_application_vals()``  addons merge extra Application fields/values.
- ``_after_launch(app)``    post-launch side work; chatter what was created.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models import k8s


class DeployWizardConfig(models.TransientModel):
    _name = "adomi.deploy.wizard.config"
    _description = "Launch wizard variable/secret line"

    wizard_id = fields.Many2one("adomi.deploy.wizard", required=True, ondelete="cascade")
    kind = fields.Selection(
        [("variable", "Variable"), ("secret", "Secret")], required=True, default="variable"
    )
    name = fields.Char(required=True)
    value = fields.Char(string="Value")


class DeployWizard(models.TransientModel):
    _name = "adomi.deploy.wizard"
    _description = "Launch Application wizard"

    state = fields.Char(default="target")
    step_label = fields.Char(compute="_compute_step_meta")
    is_first_step = fields.Boolean(compute="_compute_step_meta")
    is_last_step = fields.Boolean(compute="_compute_step_meta")

    # --- step: target (who + where) ---
    # Cascading: organization narrows the customers, customer narrows the
    # environments. New records are made inline with Odoo's native quick-create
    # ("Create ..." / "Create and edit") on the same fields — no parallel
    # "…or new" inputs.
    organization_id = fields.Many2one("adomi.organization", string="Organization")
    client_id = fields.Many2one(
        "adomi.client",
        string="Customer",
        domain="[('organization_id', '=?', organization_id)]",
    )
    environment_id = fields.Many2one(
        "adomi.environment",
        string="Environment",
        domain="[('client_id', '=', client_id)]",
    )

    # --- step: app (what) ---
    type_id = fields.Many2one("adomi.application.type", string="Application type")
    app_name = fields.Char(string="Application name")
    hostname = fields.Char(string="Hostname", help="Override the generated ingress host.")
    type_needs_database = fields.Boolean(related="type_id.database_required")

    # --- step: database (only when the type needs one) ---
    database_server_id = fields.Many2one(
        "adomi.database.server",
        string="Database server",
        domain="['|', ('client_id', '=', client_id), ('client_id', '=', False)]",
    )
    database_name = fields.Char(
        string="Database", help="Defaults to the application name (slugged)."
    )

    # --- step: config (variables & secrets at app scope) ---
    config_line_ids = fields.One2many("adomi.deploy.wizard.config", "wizard_id")

    # --- review summary ---
    review_summary = fields.Html(compute="_compute_review_summary")

    # ------------------------------------------------------------------ steps
    def _wizard_steps(self):
        """Ordered (key, label) steps. Product addons inject theirs here."""
        return [
            ("target", _("Customer & environment")),
            ("app", _("Application")),
            ("database", _("Database")),
            ("config", _("Variables & secrets")),
            ("review", _("Review & launch")),
        ]

    def _step_visible(self, step):
        """Whether a step applies given the current choices."""
        if step == "database":
            return bool(self.type_needs_database)
        return True

    def _visible_steps(self):
        return [s for s, _label in self._wizard_steps() if self._step_visible(s)]

    @api.depends("state", "type_id")
    def _compute_step_meta(self):
        for rec in self:
            steps = rec._visible_steps()
            labels = dict(rec._wizard_steps())
            state = rec.state if rec.state in steps else steps[0]
            rec.step_label = labels.get(state, state)
            rec.is_first_step = state == steps[0]
            rec.is_last_step = state == steps[-1]

    def _goto(self, offset):
        self.ensure_one()
        steps = self._visible_steps()
        idx = steps.index(self.state) if self.state in steps else 0
        self.state = steps[max(0, min(len(steps) - 1, idx + offset))]
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_next(self):
        self._validate_step(self.state)
        return self._goto(+1)

    def action_back(self):
        return self._goto(-1)

    def _validate_step(self, step):
        if step == "target":
            if not self.client_id:
                raise UserError(_("Pick a customer (or create one right in the field)."))
            if not self.environment_id:
                raise UserError(_("Pick an environment (or create one right in the field)."))
        if step == "app":
            if not self.type_id:
                raise UserError(_("Choose an application from the catalog."))
            if not self.app_name:
                raise UserError(_("Give the application a name."))

    # ------------------------------------------------------------------ defaults
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)

        orgs = self.env["adomi.organization"].search([])
        if len(orgs) == 1:
            vals.setdefault("organization_id", orgs.id)

        # Guided-setup cards preselect a catalog entry by its k8s name.
        type_slug = self.env.context.get("default_type_k8s_name")
        if type_slug and not vals.get("type_id"):
            app_type = self.env["adomi.application.type"].search(
                [("k8s_name", "=", type_slug)], limit=1
            )
            if app_type:
                vals["type_id"] = app_type.id

        return vals

    @api.onchange("organization_id")
    def _onchange_organization_id(self):
        if (
            self.organization_id
            and self.client_id.organization_id
            and self.client_id.organization_id != self.organization_id
        ):
            self.client_id = False

    @api.onchange("client_id")
    def _onchange_client_id(self):
        if self.client_id:
            if self.client_id.organization_id:
                self.organization_id = self.client_id.organization_id
            if self.environment_id and self.environment_id.client_id != self.client_id:
                self.environment_id = False

    # ------------------------------------------------------------------ review
    def _review_lines(self):
        """(label, value) pairs for the review step; addons append theirs."""
        self.ensure_one()
        lines = [
            (_("Customer"), self.client_id.name or ""),
            (_("Environment"), self.environment_id.name or ""),
            (_("Application"), "%s (%s)" % (self.app_name or "", self.type_id.name or "")),
        ]
        if self.hostname:
            lines.append((_("Hostname"), self.hostname))
        if self.type_needs_database and self.database_server_id:
            lines.append((_("Database"), "%s on %s" % (
                self.database_name or k8s.slugify(self.app_name or ""),
                self.database_server_id.name,
            )))
        if self.config_line_ids:
            lines.append((
                _("Variables & secrets"),
                ", ".join(self.config_line_ids.mapped("name")),
            ))
        return lines

    @api.depends("state")
    def _compute_review_summary(self):
        for rec in self:
            rows = "".join(
                "<tr><td class='fw-bold pe-3'>%s</td><td>%s</td></tr>" % pair
                for pair in rec._review_lines()
            )
            rec.review_summary = "<table class='table table-sm mb-0'>%s</table>" % rows

    # ------------------------------------------------------------------ launch
    def _prepare_application_vals(self, client, environment):
        """The Application record. Product addons extend (edition, values, …)."""
        slug = k8s.slugify(self.app_name)
        vals = {
            "name": self.app_name,
            "k8s_name": slug,
            "environment_id": environment.id,
            "type_id": self.type_id.id,
            "hostname": self.hostname or False,
        }
        if self.type_needs_database and self.database_server_id:
            vals["database_ids"] = [
                (0, 0, {
                    "name": slug,
                    "server_name": self.database_server_id.k8s_name,
                    "database_name": self.database_name or slug,
                    "secret": "%s-db" % slug,
                })
            ]
        return vals

    def _after_launch(self, application):
        """Post-launch side work. Addons override; chatter their resources."""
        for line in self.config_line_ids:
            self.env["adomi.scoped.config"].create(
                {
                    "name": line.name,
                    "kind": line.kind,
                    "value": line.value if line.kind == "variable" else False,
                    "secret_value": line.value if line.kind == "secret" else False,
                    "application_id": application.id,
                }
            )

    def action_launch(self):
        self.ensure_one()
        self._validate_step("target")
        self._validate_step("app")

        application = self.env["adomi.application"].create(
            self._prepare_application_vals(self.client_id, self.environment_id)
        )
        self._after_launch(application)

        return {
            "type": "ir.actions.act_window",
            "name": _("Application"),
            "res_model": "adomi.application",
            "res_id": application.id,
            "view_mode": "form",
            "target": "current",
        }

    # Kept for backward compatibility with existing buttons/actions.
    def action_deploy(self):
        return self.action_launch()
