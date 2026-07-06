from odoo import fields, models


class GitRepository(models.Model):
    _name = "adomi.git.repository"
    _description = "Adomi Git Repository"
    _inherit = ["adomi.k8s.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "gitrepositories"
    _k8s_kind = "GitRepository"

    name = fields.Char(required=True, tracking=True)
    client_id = fields.Many2one(
        "adomi.client",
        string="Customer",
        ondelete="cascade",
        index=True,
        help="The customer whose applications build from this repository. The "
        "GitRepository resource is committed to that customer's infrastructure "
        "repo, next to the applications that reference it — without a customer "
        "it lands in the shared platform namespace, where customer apps cannot "
        "resolve it.",
    )
    url = fields.Char(required=True, help="https or ssh URL, e.g. https://github.com/acme/erp")
    default_branch = fields.Char(default="main")
    credentials_secret = fields.Char(
        string="Credentials secret",
        help="Name of a Kubernetes Secret holding a token (key 'token').",
    )
    preview_enabled = fields.Boolean(string="PR previews")
    preview_client_id = fields.Many2one("adomi.client", string="Preview client")
    preview_application_type = fields.Char(string="Preview app type", default="odoo")

    def _k8s_client_slug(self):
        return self.client_id.k8s_name or False

    def _k8s_identity_domain(self, obj):
        # The same repository name may exist under several customers: identity
        # is (client, name), like domains and database servers.
        domain = super()._k8s_identity_domain(obj)
        slug = self._k8s_obj_client_slug(obj)
        if slug:
            domain.append(("client_id.k8s_name", "=", slug))
        return domain

    def _api_body(self):
        self.ensure_one()

        body = {"url": self.url, "default_branch": self.default_branch or "main"}

        if self.credentials_secret:
            body["credentials_secret"] = self.credentials_secret

        if self.preview_enabled:
            preview = {"enabled": True}
            if self.preview_client_id:
                preview["client"] = self.preview_client_id.k8s_name
            if self.preview_application_type:
                preview["application_type"] = self.preview_application_type
            body["preview"] = preview

        return body

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
