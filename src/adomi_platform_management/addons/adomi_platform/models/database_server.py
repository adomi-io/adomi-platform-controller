from odoo import api, fields, models


class DatabaseServer(models.Model):
    """A standalone database server (CloudNativePG in-cluster, or an external
    managed server) that lives under a customer / environment. Applications
    attach databases to it explicitly via their `databases` capability list —
    the server is never auto-created or inferred.
    """

    _name = "adomi.database.server"
    _description = "Adomi Database Server"
    _inherit = ["adomi.k8s.mixin", "mail.thread"]
    _rec_name = "name"

    _k8s_plural = "databaseservers"
    _k8s_kind = "DatabaseServer"

    name = fields.Char(required=True, tracking=True)
    client_id = fields.Many2one(
        "adomi.client",
        string="Customer",
        ondelete="cascade",
        index=True,
        help="The customer (client) this server belongs to. Customer-owned servers "
        "are committed to that customer's git repo; leave empty for a shared one.",
    )
    environment_id = fields.Many2one(
        "adomi.environment",
        string="Environment",
        ondelete="set null",
        help="Optional: scope the server to one environment (environment).",
    )
    engine = fields.Selection(
        [("postgres", "PostgreSQL")],
        string="Engine",
        default="postgres",
        required=True,
    )
    mode = fields.Selection(
        [
            ("cnpg", "In-cluster (CloudNativePG)"),
            ("external", "External (managed)"),
        ],
        string="Mode",
        default="cnpg",
        required=True,
        tracking=True,
        help="cnpg: the platform provisions an in-cluster CloudNativePG cluster. "
        "external: the platform only connects to a server you run (RDS, DO, …).",
    )

    # --- cnpg (mode = cnpg) ---
    cnpg_storage = fields.Char(string="Storage", default="10Gi")
    cnpg_storage_class = fields.Char(string="Storage class")
    cnpg_instances = fields.Integer(string="Instances", default=1)

    # --- external (mode = external) ---
    external_host = fields.Char(string="Host")
    external_port = fields.Integer(string="Port", default=5432)
    external_ssl_mode = fields.Char(string="SSL mode", help="e.g. require, verify-full")

    # --- admin credentials (where the superuser/admin password lives) ---
    admin_user = fields.Char(string="Admin user")
    admin_openbao_path = fields.Char(string="OpenBao path")

    # --- status (read from the cluster) ---
    host = fields.Char(string="Resolved host", readonly=True, copy=False)

    def _k8s_client_slug(self):
        return self.client_id.k8s_name or False

    def _api_body(self):
        self.ensure_one()

        body = {"engine": self.engine, "mode": self.mode}

        if self.mode == "cnpg":
            if self.cnpg_storage:
                body["storage"] = self.cnpg_storage
            if self.cnpg_storage_class:
                body["storage_class"] = self.cnpg_storage_class
            if self.cnpg_instances:
                body["instances"] = self.cnpg_instances
        elif self.mode == "external":
            if self.external_host:
                body["host"] = self.external_host
            if self.external_port:
                body["port"] = self.external_port
            if self.external_ssl_mode:
                body["ssl_mode"] = self.external_ssl_mode

        if self.admin_user:
            body["admin_user"] = self.admin_user
        if self.admin_openbao_path:
            body["admin_openbao_path"] = self.admin_openbao_path

        if self.environment_id:
            body["environment"] = self.environment_id.k8s_name

        return body

    def _k8s_spec(self):
        self.ensure_one()

        spec = {"engine": self.engine, "mode": self.mode}

        if self.mode == "cnpg":
            cnpg = {}
            if self.cnpg_storage:
                cnpg["storage"] = self.cnpg_storage
            if self.cnpg_storage_class:
                cnpg["storageClass"] = self.cnpg_storage_class
            if self.cnpg_instances:
                cnpg["instances"] = self.cnpg_instances
            if cnpg:
                spec["cnpg"] = cnpg
        elif self.mode == "external":
            external = {}
            if self.external_host:
                external["host"] = self.external_host
            if self.external_port:
                external["port"] = self.external_port
            if self.external_ssl_mode:
                external["sslMode"] = self.external_ssl_mode
            if external:
                spec["external"] = external

        admin = {}
        if self.admin_user:
            admin["user"] = self.admin_user
        if self.admin_openbao_path:
            admin["openbaoPath"] = self.admin_openbao_path
        if admin:
            spec["admin"] = admin

        if self.environment_id:
            spec["environmentRef"] = {"name": self.environment_id.k8s_name}

        return spec

    def _k8s_status_vals(self, obj):
        return {
            "host": (obj.get("status") or {}).get("host") or False,
        }

    def _k8s_import_vals(self, obj):
        spec = obj.get("spec") or {}
        cnpg = spec.get("cnpg") or {}
        ext = spec.get("external") or {}
        admin = spec.get("admin") or {}
        env_ref = (spec.get("environmentRef") or {}).get("name")
        environment = (
            self.env["adomi.environment"].search([("k8s_name", "=", env_ref)], limit=1)
            if env_ref
            else self.env["adomi.environment"]
        )
        return {
            "name": (obj.get("metadata") or {}).get("name"),
            "engine": spec.get("engine") or "postgres",
            "mode": spec.get("mode") or "cnpg",
            "cnpg_storage": cnpg.get("storage") or False,
            "cnpg_storage_class": cnpg.get("storageClass") or False,
            "cnpg_instances": cnpg.get("instances") or 1,
            "external_host": ext.get("host") or False,
            "external_port": ext.get("port") or 5432,
            "external_ssl_mode": ext.get("sslMode") or False,
            "admin_user": admin.get("user") or False,
            "admin_openbao_path": admin.get("openbaoPath") or False,
            "environment_id": environment.id or False,
            "client_id": environment.client_id.id if environment else False,
        }

    @api.onchange("client_id")
    def _onchange_client_scope_environment(self):
        # Keep the environment within the selected customer.
        for rec in self:
            if rec.environment_id and rec.environment_id.client_id != rec.client_id:
                rec.environment_id = False
