import json

from odoo import _, api, fields, models


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
    scoped_config_ids = fields.One2many(
        "adomi.scoped.config", "application_id", string="Variables & Secrets"
    )
    environment_id = fields.Many2one(
        "adomi.environment", string="Environment", required=True, ondelete="cascade"
    )
    # Stored so Applications can be grouped / filtered / rolled up per customer
    # without hopping through the environment each time (Client is the SaaS unit).
    client_id = fields.Many2one(
        "adomi.client",
        string="Customer",
        related="environment_id.client_id",
        store=True,
        index=True,
        readonly=True,
    )
    type_id = fields.Many2one("adomi.application.type", string="Type", required=True)
    replicas = fields.Integer(string="Replicas", default=1)

    # --- where the app is published: [subdomain].[domain], or a raw override ---
    domain_id = fields.Many2one(
        "adomi.domain",
        string="Domain",
        domain="[('client_id', '=', client_id)]",
        help="The customer domain this app is published under.",
    )
    subdomain = fields.Char(
        string="Subdomain",
        help="Label under the selected domain: <subdomain>.<domain>.",
    )
    hostname = fields.Char(
        string="Hostname override",
        help="Advanced: full ingress host, bypassing the subdomain + domain pair.",
    )
    host_effective = fields.Char(
        compute="_compute_host_effective",
        string="Host",
        help="The host this app is (or will be) published at.",
    )

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

    @api.depends("hostname", "subdomain", "domain_id.fqdn", "url")
    def _compute_host_effective(self):
        for rec in self:
            rec.host_effective = rec._composed_host() or (
                rec.url.replace("https://", "").rstrip("/") if rec.url else False
            )

    def _composed_host(self):
        """The explicit ingress host this record asks for (False = generated)."""
        self.ensure_one()
        if self.hostname:
            return self.hostname.strip().lower()
        if self.subdomain and self.domain_id.fqdn:
            return ("%s.%s" % (self.subdomain.strip(), self.domain_id.fqdn.strip())).lower()
        return False

    @api.onchange("environment_id")
    def _onchange_environment_scope_domain(self):
        # Keep the domain within the app's customer.
        for rec in self:
            if rec.domain_id and rec.domain_id.client_id != rec.environment_id.client_id:
                rec.domain_id = False

    def _k8s_client_slug(self):
        return self.client_id.k8s_name or False

    def _api_path(self):
        # Applications are nested under their environment:
        # PUT /v1/clients/{client}/environments/{environment}/applications/{name}.
        self.ensure_one()
        return "/v1/clients/%s/environments/%s/applications/%s" % (
            self._k8s_client_slug(),
            self.environment_id.k8s_name,
            self.k8s_name,
        )

    def _api_body(self):
        self.ensure_one()

        body = {
            "type": self.type_id.k8s_name,
            "display_name": self.name,
        }

        databases = [d._spec() for d in self.database_ids]
        if databases:
            body["databases"] = databases

        sso = [s._spec() for s in self.sso_ids]
        if sso:
            body["sso"] = sso

        env = [e._spec() for e in self.env_ids]
        if env:
            body["env"] = env

        if self.replicas:
            body["replicas"] = self.replicas

        host = self._composed_host()
        if host:
            body["host"] = host

        if self.domain_id:
            body["domain"] = self.domain_id.k8s_name

        values = self._parse_values(self.values)
        if values:
            body["values"] = values

        if self.git_repository_id:
            source = {"repository": self.git_repository_id.k8s_name}
            if self.source_ref:
                source["ref"] = self.source_ref
            body["source"] = source

        return body

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
            "environmentRef": {"name": self.environment_id.k8s_name},
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

        host = self._composed_host()
        if host:
            spec["ingress"] = {"host": host}

        if self.domain_id:
            spec["domainRef"] = {"name": self.domain_id.k8s_name}

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

    def get_effective_config(self):
        """The rolled-up Variables & Secrets this app's workload receives.

        Walks the scope chain (organization -> customer -> environment ->
        application); nearer scopes override by name. Overridden entries stay in
        the result flagged, so the UI can show WHERE a value comes from and what
        it shadowed (the GitHub Actions roll-up view). Secret values are never
        included - only names.
        """
        self.ensure_one()

        chain = [
            ("organization", self.client_id.organization_id),
            ("client", self.client_id),
            ("environment", self.environment_id),
            ("application", self),
        ]

        winners = {}
        entries = []
        for scope, owner in chain:
            for rec in owner.scoped_config_ids if owner else []:
                entry = {
                    "id": rec.id,
                    "name": rec.name,
                    "kind": rec.kind,
                    "value": rec.value if rec.kind == "variable" else False,
                    "scope": scope,
                    "overridden": False,
                }
                if rec.name in winners:
                    winners[rec.name]["overridden"] = True
                winners[rec.name] = entry
                entries.append(entry)

        return sorted(entries, key=lambda e: (e["name"], e["overridden"]))

    def _k8s_identity_domain(self, obj):
        # "superset" exists in every client: identity is (client, environment, name).
        domain = super()._k8s_identity_domain(obj)
        slug = self._k8s_obj_client_slug(obj)
        if slug:
            domain.append(("environment_id.client_id.k8s_name", "=", slug))
        env_ref = ((obj.get("spec") or {}).get("environmentRef") or {}).get("name")
        if env_ref:
            domain.append(("environment_id.k8s_name", "=", env_ref))
        return domain

    def _k8s_import_vals(self, obj):
        spec = obj.get("spec") or {}

        ws_ref = (spec.get("environmentRef") or {}).get("name")
        env_domain = [("k8s_name", "=", ws_ref)]
        slug = self._k8s_obj_client_slug(obj)
        if slug:
            env_domain.append(("client_id.k8s_name", "=", slug))
        environment = (
            self.env["adomi.environment"].search(env_domain, limit=1)
            if ws_ref
            else self.env["adomi.environment"]
        )
        if not environment:
            return None  # the environment must be imported first

        type_ref = spec.get("type")
        app_type = (
            self.env["adomi.application.type"].search([("k8s_name", "=", type_ref)], limit=1)
            if type_ref
            else self.env["adomi.application.type"]
        )
        if not app_type:
            return None  # type is required — import the catalog first

        vals = {
            "name": (obj.get("metadata") or {}).get("name"),
            "environment_id": environment.id,
            "type_id": app_type.id,
            "replicas": spec.get("replicas") or 1,
        }

        domain_ref = (spec.get("domainRef") or {}).get("name")
        domain = (
            self.env["adomi.domain"].search(
                [
                    ("k8s_name", "=", domain_ref),
                    ("client_id", "=", environment.client_id.id),
                ],
                limit=1,
            )
            if domain_ref
            else self.env["adomi.domain"]
        )
        vals["domain_id"] = domain.id or False

        # A host under the referenced domain round-trips as subdomain + domain;
        # anything else stays a raw override.
        host = ((spec.get("ingress") or {}).get("host") or "").strip().lower()
        fqdn = (domain.fqdn or "").strip().lower()
        if host and fqdn and host.endswith("." + fqdn):
            vals["subdomain"] = host[: -len(fqdn) - 1]
            vals["hostname"] = False
        else:
            vals["subdomain"] = False
            vals["hostname"] = host or False

        vals["database_ids"] = [
            (0, 0, {
                "name": d.get("name"),
                "server_name": d.get("server") or False,
                "database_name": d.get("databaseName") or False,
                "user": d.get("user") or False,
                "secret": (d.get("credentials") or {}).get("secret") or False,
            })
            for d in spec.get("databases") or []
        ]

        vals["sso_ids"] = [
            (0, 0, {
                "name": s.get("name"),
                "protocol": s.get("protocol") or "oauth2",
                "redirect_uris": "\n".join(s.get("redirectUris") or []) or False,
                "secret": (s.get("credentials") or {}).get("secret") or False,
            })
            for s in spec.get("sso") or []
        ]

        vals["env_ids"] = [
            (0, 0, {
                "name": e.get("name"),
                "value": e.get("value") or False,
                "secret_name": ((e.get("valueFrom") or {}).get("secretKeyRef") or {}).get("name")
                or False,
                "secret_key": ((e.get("valueFrom") or {}).get("secretKeyRef") or {}).get("key")
                or False,
            })
            for e in spec.get("env") or []
        ]

        values = spec.get("values")
        if values:
            vals["values"] = json.dumps(values, indent=2)

        source = spec.get("source") or {}
        repo_ref = (source.get("repositoryRef") or {}).get("name")
        if repo_ref:
            repo = self.env["adomi.git.repository"].search([("k8s_name", "=", repo_ref)], limit=1)
            if repo:
                vals["git_repository_id"] = repo.id
            vals["source_ref"] = source.get("ref") or False

        return vals

    # --- who can reach this app (Authentik, via the platform API) ---
    def get_access(self):
        """Current access state: everyone-with-SSO, or the granted users."""
        self.ensure_one()

        if self._k8s_write_backend() != "api" or not self.k8s_name:
            return {"available": False, "reason": "no_api"}

        from .api_client import PlatformApiError

        try:
            return self._platform_api().get(self._api_path() + "/access")
        except PlatformApiError as exc:
            return {"available": False, "reason": "error", "error": str(exc)}

    def action_revoke_access(self, user_pk):
        """Remove one user; revoking the last one opens the app up again."""
        self.ensure_one()
        self._platform_api().delete(self._api_path() + "/access/%s" % int(user_pk))
        return True

    def action_open_access_dialog(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": _("Who can open %s?") % self.name,
            "res_model": "adomi.app.access.wizard",
            "view_mode": "form",
            # Fed straight to doAction by the customer portal, which does not
            # normalize view_mode into views itself.
            "views": [[False, "form"]],
            "target": "new",
            "context": {"default_application_id": self.id},
        }

    def action_open_host_dialog(self):
        """The customer portal's host editor: subdomain + domain, in a dialog."""
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": _("Where is %s published?") % self.name,
            "res_model": "adomi.application",
            "res_id": self.id,
            "view_mode": "form",
            "views": [
                (self.env.ref("adomi_platform.view_adomi_application_host_form").id, "form")
            ],
            "target": "new",
        }

    # --- observability hooks ---
    def _obs_pod_regex(self):
        # This application's pods only, not everything sharing the environment
        # namespace. The Helm release is the Argo app (<namespace>-<app>) and its
        # fullname prefixes every pod, across all the release's components.
        self.ensure_one()
        release = self._obs_argocd_app()
        return "%s-.*" % release if release else ""

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
