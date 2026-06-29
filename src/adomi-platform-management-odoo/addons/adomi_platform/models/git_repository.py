from odoo import fields, models


class GitRepository(models.Model):
    _name = "adomi.git.repository"
    _description = "Adomi Git Repository"
    _inherit = ["adomi.k8s.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "gitrepositories"
    _k8s_kind = "GitRepository"

    name = fields.Char(required=True, tracking=True)
    url = fields.Char(required=True, help="https or ssh URL, e.g. https://github.com/acme/erp")
    default_branch = fields.Char(default="main")
    credentials_secret = fields.Char(
        string="Credentials secret",
        help="Name of a Kubernetes Secret holding a token (key 'token').",
    )
    preview_enabled = fields.Boolean(string="PR previews")
    preview_client_id = fields.Many2one("adomi.client", string="Preview client")
    preview_application_type = fields.Char(string="Preview app type", default="odoo")

    def _k8s_spec(self):
        self.ensure_one()

        spec = {"url": self.url, "defaultBranch": self.default_branch or "main"}

        if self.credentials_secret:
            spec["credentialsSecretRef"] = {"name": self.credentials_secret}

        if self.preview_enabled:
            preview = {"enabled": True}

            if self.preview_client_id:
                preview["clientRef"] = {"name": self.preview_client_id.k8s_name}

            if self.preview_application_type:
                preview["applicationType"] = self.preview_application_type

            spec["preview"] = preview

        return spec
