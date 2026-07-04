"""The Odoo product step of the launch wizard.

Injected between "Application" and the database step whenever the chosen catalog
entry is Odoo: pick the edition (Community/Enterprise), optionally generate the
customer's pipeline repository from the adomi-io/odoo-boilerplate template on
THEIR GitHub (via the installed GitHub App), and — for Enterprise — point at
their enterprise mirror. Everything lands on the Application (edition in chart
values, the generated repo linked as its build source) and is chattered so the
user can see every resource the wizard created.
"""

import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

BOILERPLATE_TEMPLATE = "adomi-io/odoo-boilerplate"


class DeployWizard(models.TransientModel):
    _inherit = "adomi.deploy.wizard"

    is_odoo_type = fields.Boolean(compute="_compute_is_odoo_type")
    odoo_edition = fields.Selection(
        [("community", "Community"), ("enterprise", "Enterprise")],
        string="Edition",
        default="community",
    )
    odoo_create_repo = fields.Boolean(
        string="Create pipeline repository",
        default=True,
        help="Generate the customer's Odoo pipeline repo from %s on their GitHub."
        % BOILERPLATE_TEMPLATE,
    )
    odoo_github_installation_id = fields.Many2one(
        "adomi.github.installation", string="GitHub account"
    )
    odoo_repo_name = fields.Char(
        string="Repository name", help="Defaults to <customer>-odoo."
    )
    odoo_enterprise_repo = fields.Char(
        string="Enterprise repository",
        help="Their GitHub mirror of odoo/enterprise (Enterprise edition only).",
    )

    @api.depends("type_id")
    def _compute_is_odoo_type(self):
        for rec in self:
            rec.is_odoo_type = rec.type_id.k8s_name == "odoo"

    # --- step injection ---------------------------------------------------------
    def _wizard_steps(self):
        steps = super()._wizard_steps()
        idx = [k for k, _label in steps].index("app") + 1
        steps.insert(idx, ("odoo", _("Odoo pipeline")))
        return steps

    def _step_visible(self, step):
        if step == "odoo":
            return bool(self.is_odoo_type)
        return super()._step_visible(step)

    def _validate_step(self, step):
        super()._validate_step(step)
        if step == "odoo" and self.is_odoo_type:
            if self.odoo_create_repo and not self.odoo_github_installation_id:
                raise UserError(
                    _("Pick the GitHub account to create the pipeline repository on, "
                      "or untick repository creation.")
                )
            if self.odoo_edition == "enterprise" and not self.odoo_enterprise_repo:
                raise UserError(
                    _("Enterprise needs your enterprise repository (a private mirror "
                      "of odoo/enterprise on your GitHub).")
                )

    # --- contribution to the Application ------------------------------------------
    def _odoo_values(self):
        values = {"odoo": {"edition": self.odoo_edition}}
        if self.odoo_edition == "enterprise" and self.odoo_enterprise_repo:
            values["odoo"]["enterpriseRepository"] = self.odoo_enterprise_repo
        return values

    def _prepare_application_vals(self, client, environment):
        vals = super()._prepare_application_vals(client, environment)
        if self.is_odoo_type:
            vals["values"] = json.dumps(self._odoo_values())
        return vals

    def _review_lines(self):
        lines = super()._review_lines()
        if self.is_odoo_type:
            lines.append((_("Odoo edition"), dict(
                self._fields["odoo_edition"].selection
            )[self.odoo_edition]))
            if self.odoo_create_repo and self.odoo_github_installation_id:
                lines.append((
                    _("Pipeline repository"),
                    "%s/%s (from %s)" % (
                        self.odoo_github_installation_id.account_login,
                        self._odoo_repo_name(),
                        BOILERPLATE_TEMPLATE,
                    ),
                ))
        return lines

    # --- post-launch: the boilerplate repo -------------------------------------------
    def _odoo_repo_name(self):
        client_name = self.client_id.k8s_name or self.new_client_name or "customer"
        from odoo.addons.adomi_platform.models import k8s

        return self.odoo_repo_name or "%s-odoo" % k8s.slugify(client_name)

    def _after_launch(self, application):
        super()._after_launch(application)
        if not (self.is_odoo_type and self.odoo_create_repo and self.odoo_github_installation_id):
            return

        installation = self.odoo_github_installation_id
        repo_name = self._odoo_repo_name()
        owner = installation.account_login

        client = installation._client()
        repo = client.generate_from_template(
            BOILERPLATE_TEMPLATE,
            repo_name,
            owner=owner,
            description=_("Odoo pipeline for %s (generated by the Adomi platform)")
            % application.client_id.name,
        )
        repo_url = repo.get("html_url") or "https://github.com/%s/%s" % (owner, repo_name)

        git_repo = self.env["adomi.git.repository"].create(
            {
                "name": "%s/%s" % (owner, repo_name),
                "k8s_name": repo_name,
                "url": repo_url,
            }
        )
        application.with_context(adomi_no_push=False).write(
            {"git_repository_id": git_repo.id}
        )
        application.message_post(
            body=_(
                "Pipeline repository <a href='%(url)s' target='_blank'>%(full)s</a> "
                "generated from %(template)s (%(edition)s edition)."
            )
            % {
                "url": repo_url,
                "full": "%s/%s" % (owner, repo_name),
                "template": BOILERPLATE_TEMPLATE,
                "edition": self.odoo_edition,
            }
        )
