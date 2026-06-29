import json

from odoo import api, fields, models


class Application(models.Model):
    _name = "adomi.application"
    _description = "Adomi Application"
    _inherit = [
        "adomi.k8s.mixin",
        "adomi.observability.mixin",
        "mail.thread",
        "mail.activity.mixin",
    ]
    _rec_name = "name"

    _k8s_plural = "applications"
    _k8s_kind = "Application"

    name = fields.Char(required=True, tracking=True)
    workspace_id = fields.Many2one(
        "adomi.workspace", string="Workspace", required=True, ondelete="cascade"
    )
    # Stored so Applications can be grouped / filtered / rolled up per customer
    # without hopping through the workspace each time (Client is the SaaS unit).
    client_id = fields.Many2one(
        "adomi.client",
        string="Customer",
        related="workspace_id.client_id",
        store=True,
        index=True,
        readonly=True,
    )
    type_id = fields.Many2one("adomi.application.type", string="Type", required=True)
    replicas = fields.Integer(string="Replicas", default=1)
    hostname = fields.Char(string="Hostname", help="Override the generated ingress host.")

    # --- PROVISION (spin it up): named capability lists -> capability CRs ---
    database_ids = fields.One2many(
        "adomi.application.database", "application_id", string="Databases"
    )
    sso_ids = fields.One2many("adomi.application.sso", "application_id", string="SSO")

    # --- CONNECT (hook it up): the workload reads only env ---
    env_ids = fields.One2many("adomi.application.env", "application_id", string="Environment")

    # Advanced: extra chart values merged last (YAML or JSON).
    values = fields.Text(string="Chart values")

    # --- build from source (optional) ---
    git_repository_id = fields.Many2one("adomi.git.repository", string="Source repo")
    source_ref = fields.Char(string="Source ref", help="Branch / tag / commit to build from.")

    # --- status (read from the cluster) ---
    url = fields.Char(string="URL", readonly=True, tracking=True)
    phase = fields.Char(string="Phase", readonly=True)
    namespace = fields.Char(string="Namespace", readonly=True)

    def _k8s_tenant_slug(self):
        return self.client_id.k8s_name or False

    @api.model
    def _parse_values(self, text):
        text = (text or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            try:
                import yaml

                return yaml.safe_load(text) or {}
            except Exception:  # noqa: BLE001 - bad values are simply ignored
                return {}

    def _k8s_spec(self):
        self.ensure_one()

        spec = {
            "workspaceRef": {"name": self.workspace_id.k8s_name},
            "type": self.type_id.k8s_name,
        }

        databases = [d._spec() for d in self.database_ids]
        if databases:
            spec["databases"] = databases

        sso = [s._spec() for s in self.sso_ids]
        if sso:
            spec["sso"] = sso

        env = [e._spec() for e in self.env_ids]
        if env:
            spec["env"] = env

        if self.replicas:
            spec["replicas"] = self.replicas

        if self.hostname:
            spec["ingress"] = {"host": self.hostname}

        values = self._parse_values(self.values)
        if values:
            spec["values"] = values

        if self.git_repository_id:
            source = {"repositoryRef": {"name": self.git_repository_id.k8s_name}}
            if self.source_ref:
                source["ref"] = self.source_ref
            spec["source"] = source

        return spec

    def _k8s_status_vals(self, obj):
        status = obj.get("status") or {}

        return {
            "url": status.get("url") or False,
            "phase": status.get("phase") or False,
            "namespace": status.get("namespace") or False,
        }

    # --- observability hooks ---
    def _obs_argocd_app(self):
        self.ensure_one()

        if not self.namespace or not self.k8s_name:
            return ""

        return ("%s-%s" % (self.namespace, self.k8s_name))[:63].rstrip("-")

    def _obs_has_source(self):
        self.ensure_one()

        return bool(self.git_repository_id)


class ApplicationDatabase(models.Model):
    """Provision: one `databases[]` entry -> the chart emits a Database CR on the
    named server and delivers the password to `credentials.secret`."""

    _name = "adomi.application.database"
    _description = "Application Database (provision)"
    _rec_name = "name"

    application_id = fields.Many2one(
        "adomi.application", string="Application", required=True, ondelete="cascade"
    )
    name = fields.Char(required=True, help="Logical name of this database on the app.")
    server_id = fields.Many2one("adomi.database.server", string="Server")
    server_name = fields.Char(
        string="Server name", help="Used when the server isn't modelled in Odoo."
    )
    database_name = fields.Char(string="Database", help="Defaults to the name.")
    user = fields.Char(string="User", help="Defaults to the name.")
    secret = fields.Char(
        string="Credentials secret",
        required=True,
        help="Kubernetes Secret the database password is delivered to (this namespace).",
    )

    def _spec(self):
        self.ensure_one()
        server = self.server_id.k8s_name or self.server_name or ""
        spec = {"name": self.name, "server": server, "credentials": {"secret": self.secret}}
        if self.database_name:
            spec["databaseName"] = self.database_name
        if self.user:
            spec["user"] = self.user
        return spec


class ApplicationSso(models.Model):
    """Provision: one `sso[]` entry -> the chart emits an SSOApplication CR and the
    controller delivers the OIDC client-id/secret to `credentials.secret`."""

    _name = "adomi.application.sso"
    _description = "Application SSO (provision)"
    _rec_name = "name"

    application_id = fields.Many2one(
        "adomi.application", string="Application", required=True, ondelete="cascade"
    )
    name = fields.Char(required=True)
    protocol = fields.Selection(
        [("oauth2", "OAuth2 / OIDC"), ("proxy", "Forward-auth proxy")],
        string="Protocol",
        default="oauth2",
        required=True,
    )
    redirect_uris = fields.Text(string="Redirect URIs", help="One URL per line.")
    secret = fields.Char(
        string="Credentials secret",
        required=True,
        help="Kubernetes Secret the client-id/client-secret are delivered to.",
    )

    def _spec(self):
        self.ensure_one()
        spec = {
            "name": self.name,
            "protocol": self.protocol,
            "credentials": {"secret": self.secret},
        }
        uris = [u.strip() for u in (self.redirect_uris or "").splitlines() if u.strip()]
        if uris:
            spec["redirectUris"] = uris
        return spec


class ApplicationEnv(models.Model):
    """Connect: one `env[]` entry. Either a literal value or a reference to a key
    in a Secret (the secret the provision side delivered). Nothing is inferred."""

    _name = "adomi.application.env"
    _description = "Application Environment Variable (connect)"
    _rec_name = "name"

    application_id = fields.Many2one(
        "adomi.application", string="Application", required=True, ondelete="cascade"
    )
    name = fields.Char(string="Name", required=True)
    value = fields.Char(string="Value")
    secret_name = fields.Char(string="From secret", help="Read the value from this Secret.")
    secret_key = fields.Char(string="Secret key", help="Defaults to 'password'.")

    def _spec(self):
        self.ensure_one()
        if self.secret_name:
            return {
                "name": self.name,
                "valueFrom": {
                    "secretKeyRef": {"name": self.secret_name, "key": self.secret_key or "password"}
                },
            }
        return {"name": self.name, "value": self.value or ""}
