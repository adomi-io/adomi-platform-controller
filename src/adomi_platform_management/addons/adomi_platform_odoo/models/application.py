"""Pipeline content management for Odoo-type applications.

The Pipeline tab turns what odoo-community-base does by hand — clone OCA repos
and copy addons into extra_addons, pip/apt install dependencies — into data on
the Application: addon sources + dependency lists. Committing writes the
declarative ``adomi-pipeline.yaml`` and the Dockerfile generated from it into
the customer's pipeline repository (their GitHub, via the installed App);
importing reads the manifest back so hand edits survive.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import pipeline


def _lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


class OdooAddonSource(models.Model):
    _name = "adomi.odoo.addon.source"
    _description = "Odoo pipeline addon source (a git repo of addons)"
    _order = "sequence, id"

    application_id = fields.Many2one(
        "adomi.application", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    repo_url = fields.Char(
        string="Repository",
        required=True,
        help="Git URL of an addons repository, e.g. https://github.com/OCA/web.git",
    )
    branch = fields.Char(default="19.0", help="Branch or tag to clone (matches the Odoo series).")
    modules = fields.Text(
        help="Addon directories to take from the repository, one per line. "
        "Leave empty to take every addon it ships."
    )

    def _pipeline_entry(self):
        self.ensure_one()

        return {
            "repo": self.repo_url,
            "branch": self.branch or "",
            "modules": _lines(self.modules),
        }


class Application(models.Model):
    _inherit = "adomi.application"

    is_odoo_type = fields.Boolean(compute="_compute_is_odoo_type")
    odoo_base_image = fields.Char(
        string="Base image",
        help="Odoo base image the pipeline builds on. Defaults to %s."
        % pipeline.DEFAULT_BASE_IMAGE,
    )
    odoo_apt_packages = fields.Text(
        string="System packages", help="Debian packages to install, one per line."
    )
    odoo_pip_packages = fields.Text(
        string="Python packages", help="pip packages to install, one per line."
    )
    odoo_addon_source_ids = fields.One2many(
        "adomi.odoo.addon.source", "application_id", string="Addon sources"
    )
    odoo_pipeline_synced_at = fields.Datetime(string="Pipeline committed", readonly=True)

    @api.depends("type_id.k8s_name")
    def _compute_is_odoo_type(self):
        for rec in self:
            rec.is_odoo_type = rec.type_id.k8s_name == "odoo"

    # --- repo plumbing ------------------------------------------------------------
    def _odoo_pipeline_repo(self):
        """(github client, full_name, branch) for the linked pipeline repo."""
        self.ensure_one()

        if not self.git_repository_id:
            raise UserError(
                _("Link a pipeline repository first (the launch wizard creates one).")
            )

        full_name = self.git_repository_id.name

        mirror = self.env["adomi.github.repository"].search(
            [("full_name", "=", full_name)], limit=1
        )

        if not mirror:
            raise UserError(
                _(
                    "No GitHub connection covers %s — install the GitHub App on that "
                    "account and sync its repositories."
                )
                % full_name
            )

        return (
            mirror.installation_id._client(),
            full_name,
            self.git_repository_id.default_branch or "main",
        )

    def _odoo_pipeline_data(self):
        self.ensure_one()

        return {
            "base_image": self.odoo_base_image or "",
            "apt": _lines(self.odoo_apt_packages),
            "pip": _lines(self.odoo_pip_packages),
            "addons": [s._pipeline_entry() for s in self.odoo_addon_source_ids],
        }

    # --- actions --------------------------------------------------------------------
    def action_odoo_commit_pipeline(self):
        """Write adomi-pipeline.yaml + the generated Dockerfile to the repo."""
        self.ensure_one()

        client, full_name, branch = self._odoo_pipeline_repo()
        data = self._odoo_pipeline_data()

        files = (
            (pipeline.MANIFEST_PATH, pipeline.render_manifest(data)),
            (pipeline.DOCKERFILE_PATH, pipeline.render_dockerfile(data)),
        )

        for path, text in files:
            current = client.get_content(full_name, path, ref=branch)

            if current and current["text"] == text:
                continue

            client.put_content(
                full_name,
                path,
                text,
                _("Update Odoo pipeline (via the Adomi portal)"),
                branch=branch,
                sha=current["sha"] if current else None,
            )

        self.write({"odoo_pipeline_synced_at": fields.Datetime.now()})
        self.message_post(
            body=_("Pipeline committed to %(repo)s (%(manifest)s + Dockerfile).")
            % {"repo": full_name, "manifest": pipeline.MANIFEST_PATH}
        )

        return True

    def action_odoo_import_pipeline(self):
        """Read adomi-pipeline.yaml from the repo into the tab (hand edits win)."""
        self.ensure_one()

        client, full_name, branch = self._odoo_pipeline_repo()
        current = client.get_content(full_name, pipeline.MANIFEST_PATH, ref=branch)

        if not current:
            raise UserError(
                _("%(repo)s has no %(manifest)s yet — commit the pipeline once first.")
                % {"repo": full_name, "manifest": pipeline.MANIFEST_PATH}
            )

        data = pipeline.parse_manifest(current["text"])

        self.write(
            {
                "odoo_base_image": data["base_image"],
                "odoo_apt_packages": "\n".join(data["apt"]),
                "odoo_pip_packages": "\n".join(data["pip"]),
                "odoo_addon_source_ids": [(5, 0, 0)]
                + [
                    (
                        0,
                        0,
                        {
                            "sequence": (index + 1) * 10,
                            "repo_url": s["repo"],
                            "branch": s["branch"],
                            "modules": "\n".join(s["modules"]),
                        },
                    )
                    for index, s in enumerate(data["addons"])
                ],
            }
        )

        return True
