import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ScopedConfig(models.Model):
    """A Variable or Secret at one scope (GitHub Actions model).

    Variables are plain values: pushed to the platform API, which commits them
    onto the scope's CR in the customer's infra repo. Secret VALUES are never
    stored in Odoo or git — they're write-only fields sent straight to the API,
    which stores them in OpenBao; only the NAME remains visible here. The
    controller merges the chain (organization < customer < environment <
    application, nearest wins) into every workload's env.
    """

    _name = "adomi.scoped.config"
    _description = "Adomi Variable / Secret"
    _order = "kind, name"

    name = fields.Char(required=True, help="Environment variable name (e.g. SMTP_HOST).")
    kind = fields.Selection(
        [("variable", "Variable"), ("secret", "Secret")],
        required=True,
        default="variable",
    )
    value = fields.Char(help="Plain value (variables only; committed to git).")
    # Write-only: intercepted before the ORM ever stores it (see create/write).
    secret_value = fields.Char(string="Secret value", store=False)
    secret_set = fields.Boolean(
        string="Value stored",
        readonly=True,
        copy=False,
        help="The secret's value is stored in the platform vault.",
    )

    organization_id = fields.Many2one("adomi.organization", ondelete="cascade", index=True)
    client_id = fields.Many2one("adomi.client", ondelete="cascade", index=True)
    environment_id = fields.Many2one("adomi.environment", ondelete="cascade", index=True)
    application_id = fields.Many2one("adomi.application", ondelete="cascade", index=True)

    scope = fields.Selection(
        [
            ("organization", "Organization"),
            ("client", "Customer"),
            ("environment", "Environment"),
            ("application", "Application"),
        ],
        compute="_compute_scope",
        store=True,
    )

    _sql_constraints = [
        (
            "name_unique_per_scope",
            "unique(name, organization_id, client_id, environment_id, application_id)",
            "This name is already defined at this scope.",
        ),
    ]

    @api.depends("organization_id", "client_id", "environment_id", "application_id")
    def _compute_scope(self):
        for rec in self:
            if rec.application_id:
                rec.scope = "application"
            elif rec.environment_id:
                rec.scope = "environment"
            elif rec.client_id:
                rec.scope = "client"
            else:
                rec.scope = "organization"

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            if not _NAME_RE.match(rec.name or ""):
                raise ValidationError(
                    _("Names must look like environment variables (letters, digits, _).")
                )

    @api.constrains("organization_id", "client_id", "environment_id", "application_id", "kind")
    def _check_scope(self):
        for rec in self:
            owners = [
                rec.organization_id,
                rec.client_id,
                rec.environment_id,
                rec.application_id,
            ]
            if len([o for o in owners if o]) != 1:
                raise ValidationError(_("Set exactly one scope (org/customer/environment/app)."))
            if rec.organization_id and rec.kind == "variable":
                # Organization variables ride the Organization CR (cluster-side),
                # pushed by the organization record itself, not the client API.
                continue

    def action_remove(self):
        """Delete from the roll-up dialog (form dialogs have no delete button)."""
        self.unlink()
        return {"type": "ir.actions.act_window_close"}

    # --- API routing ---------------------------------------------------------------
    def _api_base(self):
        self.ensure_one()
        if self.application_id:
            app = self.application_id
            return "/v1/clients/%s/environments/%s/applications/%s" % (
                app.client_id.k8s_name,
                app.environment_id.k8s_name,
                app.k8s_name,
            )
        if self.environment_id:
            env = self.environment_id
            return "/v1/clients/%s/environments/%s" % (env.client_id.k8s_name, env.k8s_name)
        if self.client_id:
            return "/v1/clients/%s" % self.client_id.k8s_name
        return "/v1/organizations/%s" % self.organization_id.k8s_name

    def _push(self, secret_value=None):
        api_client = self.env["adomi.k8s.mixin"]._platform_api()
        for rec in self:
            if rec.organization_id and rec.kind == "variable":
                # Variables on the org live on the Organization CR.
                rec.organization_id._k8s_push()
                continue
            if rec.kind == "variable":
                api_client.upsert(
                    "%s/variables/%s" % (rec._api_base(), rec.name),
                    {"value": rec.value or ""},
                )
            else:
                if secret_value is None:
                    continue  # metadata-only edit; the stored value stands
                api_client.upsert(
                    "%s/secrets/%s" % (rec._api_base(), rec.name),
                    {"value": secret_value},
                )
                rec.with_context(adomi_config_no_push=True).write({"secret_set": True})

    def _remove_remote(self):
        api_client = self.env["adomi.k8s.mixin"]._platform_api()
        for rec in self:
            if rec.organization_id and rec.kind == "variable":
                continue  # handled by the org push after unlink
            suffix = "variables" if rec.kind == "variable" else "secrets"
            api_client.delete("%s/%s/%s" % (rec._api_base(), suffix, rec.name))

    # --- ORM overrides ---------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        secret_values = [vals.pop("secret_value", None) for vals in vals_list]
        records = super().create(vals_list)
        if not self.env.context.get("adomi_config_no_push"):
            for rec, secret in zip(records, secret_values):
                if rec.kind == "secret" and not secret:
                    raise UserError(_("Provide a value for the secret %s.") % rec.name)
                rec._push(secret_value=secret)
        return records

    def write(self, vals):
        secret = vals.pop("secret_value", None)
        res = super().write(vals)
        if not self.env.context.get("adomi_config_no_push"):
            self._push(secret_value=secret)
        return res

    def unlink(self):
        if not self.env.context.get("adomi_config_no_push"):
            self._remove_remote()
        orgs = self.filtered(lambda r: r.organization_id and r.kind == "variable").mapped(
            "organization_id"
        )
        res = super().unlink()
        for org in orgs:
            org._k8s_push()
        return res
