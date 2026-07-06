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
    odoo_repo_mode = fields.Selection(
        [
            ("generate", "Create a new repository for this customer (recommended)"),
            ("existing", "Use a repository you already have"),
            ("none", "Decide later"),
        ],
        string="Code repository",
        default="generate",
        required=True,
        help="Every Odoo project lives in a git repository: its addons, "
        "configuration and build. New repositories are created ready-to-go on "
        "the customer's GitHub (from %s)." % BOILERPLATE_TEMPLATE,
    )
    odoo_github_installation_id = fields.Many2one(
        "adomi.github.installation", string="GitHub account"
    )
    odoo_repo_name = fields.Char(
        string="Repository name", help="Defaults to <customer>-odoo."
    )
    odoo_existing_repo_id = fields.Many2one(
        "adomi.github.repository",
        string="Repository",
        domain="[('installation_id', '=', odoo_github_installation_id)]",
    )
    odoo_enterprise_repo = fields.Char(
        string="Enterprise repository",
        help="Their GitHub mirror of odoo/enterprise (Enterprise edition only).",
    )

    @api.depends("type_id")
    def _compute_is_odoo_type(self):
        for rec in self:
            rec.is_odoo_type = rec.type_id.k8s_name == "odoo"

    @api.onchange("odoo_github_installation_id", "odoo_repo_mode")
    def _onchange_odoo_repo_source(self):
        if self.odoo_existing_repo_id.installation_id != self.odoo_github_installation_id:
            self.odoo_existing_repo_id = False
        # Refresh the picker so it offers the account's current repositories.
        # Best-effort: a GitHub hiccup leaves the last-synced list in place.
        if self.odoo_repo_mode == "existing" and self.odoo_github_installation_id:
            try:
                self.odoo_github_installation_id.action_sync_repos()
            except Exception:  # noqa: BLE001
                pass

    # --- step injection ---------------------------------------------------------
    def _wizard_steps(self):
        steps = super()._wizard_steps()
        idx = [k for k, _label in steps].index("app") + 1
        steps.insert(idx, ("odoo", _("Your Odoo project")))
        return steps

    def _step_visible(self, step):
        if step == "odoo":
            return bool(self.is_odoo_type)
        return super()._step_visible(step)

    def _validate_step(self, step):
        super()._validate_step(step)
        if step == "odoo" and self.is_odoo_type:
            if self.odoo_repo_mode != "none" and not self.odoo_github_installation_id:
                raise UserError(
                    _("Pick the GitHub account for the pipeline repository, "
                      "or skip the repository for now.")
                )
            if self.odoo_repo_mode == "existing" and not self.odoo_existing_repo_id:
                raise UserError(_("Pick the existing repository to use."))
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
            if self.odoo_repo_mode == "generate" and self.odoo_github_installation_id:
                lines.append((
                    _("Pipeline repository"),
                    "%s/%s (from %s)" % (
                        self.odoo_github_installation_id.account_login,
                        self._odoo_repo_name(),
                        BOILERPLATE_TEMPLATE,
                    ),
                ))
            elif self.odoo_repo_mode == "existing" and self.odoo_existing_repo_id:
                lines.append((
                    _("Pipeline repository"),
                    self.odoo_existing_repo_id.full_name,
                ))
        return lines

    # --- post-launch: the pipeline repo ----------------------------------------------
    def _odoo_repo_name(self):
        client_name = self.client_id.k8s_name or "customer"
        from odoo.addons.adomi_platform.models import k8s

        return self.odoo_repo_name or "%s-odoo" % k8s.slugify(client_name)

    def _link_pipeline_repo(self, application, full_name, repo_url, default_branch, note):
        """Register the repo as a platform GitRepository and wire it to the app."""
        from odoo.addons.adomi_platform.models import k8s

        git_repo = self.env["adomi.git.repository"].create(
            {
                "name": full_name,
                "k8s_name": k8s.slugify(full_name.rsplit("/", 1)[-1]),
                # Scoped to the customer: the GitRepository CR is committed to
                # their infrastructure repo, where the application resolves it.
                "client_id": application.client_id.id,
                "url": repo_url,
                "default_branch": default_branch or "main",
            }
        )
        application.with_context(adomi_no_push=False).write(
            {"git_repository_id": git_repo.id}
        )
        application.message_post(
            body=_(
                "Pipeline repository <a href='%(url)s' target='_blank'>%(full)s</a> %(note)s."
            )
            % {"url": repo_url, "full": full_name, "note": note}
        )
        return git_repo

    def _after_launch(self, application):
        super()._after_launch(application)
        if not self.is_odoo_type:
            return

        # The product's own record of this deployment: edition + repo + app in
        # one place (Odoo -> Projects), the anchor for the product dashboard.
        project = self.env["adomi.odoo.project"].create(
            {
                "name": "%s — %s" % (self.client_id.name, application.name),
                "application_id": application.id,
                "edition": self.odoo_edition,
            }
        )
        project.message_post(
            body=_("Created by the launch wizard (%s edition).") % self.odoo_edition
        )

        if self.odoo_repo_mode == "existing" and self.odoo_existing_repo_id:
            repo = self.odoo_existing_repo_id
            self._link_pipeline_repo(
                application,
                repo.full_name,
                repo.html_url or "https://github.com/%s" % repo.full_name,
                repo.default_branch,
                _("linked (%s edition)") % self.odoo_edition,
            )
            return

        if not (self.odoo_repo_mode == "generate" and self.odoo_github_installation_id):
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
        self._link_pipeline_repo(
            application,
            "%s/%s" % (owner, repo_name),
            repo_url,
            repo.get("default_branch"),
            _("generated from %(template)s (%(edition)s edition)")
            % {"template": BOILERPLATE_TEMPLATE, "edition": self.odoo_edition},
        )
