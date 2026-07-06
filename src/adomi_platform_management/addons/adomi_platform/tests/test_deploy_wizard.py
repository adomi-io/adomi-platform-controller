"""The core launch wizard: cascading target step, quick-create, launch."""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestDeployWizard(TransactionCase):
    def setUp(self):
        super().setUp()
        self.no_push = self.env(context={"adomi_no_push": True, "adomi_config_no_push": True})
        self.org = self.no_push["adomi.organization"].create(
            {"name": "Adomi", "k8s_name": "adomi"}
        )
        self.other_org = self.no_push["adomi.organization"].create(
            {"name": "Other", "k8s_name": "other"}
        )
        self.client = self.no_push["adomi.client"].create(
            {"name": "Acme", "k8s_name": "acme", "organization_id": self.org.id}
        )
        self.environment = self.no_push["adomi.environment"].create(
            {"name": "Prod", "k8s_name": "production", "client_id": self.client.id}
        )
        self.app_type = self.no_push["adomi.application.type"].create(
            {"name": "Uptime", "k8s_name": "uptime-kuma-wiz"}
        )

    def _wizard(self, **vals):
        return self.no_push["adomi.deploy.wizard"].create(vals)

    # --- quick-create: the replacement for the "…or new" fields ---
    def test_quick_create_customer_derives_resource_name(self):
        client_id, _display = self.no_push["adomi.client"].name_create("Acme Rockets")
        client = self.env["adomi.client"].browse(client_id)
        self.assertEqual(client.k8s_name, "acme-rockets")

    def test_quick_create_environment_uses_context_client(self):
        env_id, _display = (
            self.no_push["adomi.environment"]
            .with_context(default_client_id=self.client.id)
            .name_create("Staging")
        )
        environment = self.env["adomi.environment"].browse(env_id)
        self.assertEqual(environment.client_id, self.client)
        self.assertEqual(environment.k8s_name, "staging")
        self.assertEqual(environment.environment_class, "development")

    # --- cascading target step ---
    def test_target_step_requires_customer_and_environment(self):
        wiz = self._wizard()
        with self.assertRaises(UserError):
            wiz._validate_step("target")
        wiz.client_id = self.client
        with self.assertRaises(UserError):
            wiz._validate_step("target")
        wiz.environment_id = self.environment
        wiz._validate_step("target")

    def test_picking_customer_derives_organization(self):
        wiz = self._wizard(client_id=self.client.id)
        wiz._onchange_client_id()
        self.assertEqual(wiz.organization_id, self.org)

    def test_changing_organization_clears_foreign_customer(self):
        wiz = self._wizard(
            organization_id=self.other_org.id,
            client_id=self.client.id,
        )
        wiz._onchange_organization_id()
        self.assertFalse(wiz.client_id)

    def test_changing_customer_clears_foreign_environment(self):
        other_client = self.no_push["adomi.client"].create(
            {"name": "Globex", "k8s_name": "globex", "organization_id": self.org.id}
        )
        wiz = self._wizard(
            client_id=self.client.id,
            environment_id=self.environment.id,
        )
        wiz.client_id = other_client
        wiz._onchange_client_id()
        self.assertFalse(wiz.environment_id)

    # --- derive, don't ask ---
    def test_deploy_action_prefills_customer_without_organization(self):
        # Kyle's repro: a customer with no organization must still arrive
        # pre-selected, and the action must carry explicit views (the customer
        # portal feeds it straight to doAction).
        lone = self.no_push["adomi.client"].create({"name": "Lone", "k8s_name": "lone"})
        action = lone.action_open_deploy_wizard()
        self.assertEqual(action["views"], [[False, "form"]])
        vals = (
            self.no_push["adomi.deploy.wizard"]
            .with_context(**action["context"])
            .default_get(["organization_id", "client_id", "environment_id"])
        )
        self.assertEqual(vals.get("client_id"), lone.id)

    def test_lone_customer_and_environment_default(self):
        vals = self.no_push["adomi.deploy.wizard"].default_get(
            ["organization_id", "client_id", "environment_id"]
        )
        self.assertEqual(vals.get("client_id"), self.client.id)
        self.assertEqual(vals.get("environment_id"), self.environment.id)

    def test_picking_customer_defaults_its_only_environment(self):
        wiz = self._wizard(client_id=self.client.id)
        wiz._onchange_client_id()
        self.assertEqual(wiz.environment_id, self.environment)

    def test_type_choice_prefills_name_and_subdomain(self):
        wiz = self._wizard(client_id=self.client.id, environment_id=self.environment.id)
        wiz.type_id = self.app_type
        wiz._onchange_type_id()
        self.assertEqual(wiz.app_name, "Uptime")
        wiz._onchange_app_name()
        self.assertEqual(wiz.subdomain, "uptime")

    # --- published address ---
    def test_host_preview_composes_subdomain_and_domain(self):
        domain = self.no_push["adomi.domain"].create(
            {"client_id": self.client.id, "fqdn": "acme.com"}
        )
        wiz = self._wizard(
            client_id=self.client.id,
            environment_id=self.environment.id,
            type_id=self.app_type.id,
            app_name="Status Page",
            subdomain="status",
            domain_id=domain.id,
        )
        self.assertEqual(wiz.host_preview, "status.acme.com")

        action = wiz.action_launch()
        application = self.env["adomi.application"].browse(action["res_id"])
        self.assertEqual(application.domain_id, domain)
        self.assertEqual(application.subdomain, "status")
        self.assertEqual(application.host_effective, "status.acme.com")

    # --- the variables step explains what's already wired ---
    def test_provided_summary_lists_platform_wiring(self):
        db_type = self.no_push["adomi.application.type"].create(
            {
                "name": "ERPish",
                "k8s_name": "erpish",
                "database_required": True,
                "sso_protocol": "oauth2",
            }
        )
        server = self.no_push["adomi.database.server"].create(
            {"name": "Acme DB", "k8s_name": "acme-db", "client_id": self.client.id}
        )
        wiz = self._wizard(
            client_id=self.client.id,
            environment_id=self.environment.id,
            type_id=db_type.id,
            app_name="ERP",
            database_server_id=server.id,
        )
        lines = dict(wiz._provided_lines())
        self.assertIn("Web address", lines)
        self.assertIn("Database connection", lines)
        self.assertIn("Acme DB", lines["Database connection"])
        self.assertIn("Single sign-on", lines)

    # --- launch ---
    def test_launch_creates_application_on_target(self):
        wiz = self._wizard(
            client_id=self.client.id,
            environment_id=self.environment.id,
            type_id=self.app_type.id,
            app_name="Status Page",
        )
        action = wiz.action_launch()
        application = self.env["adomi.application"].browse(action["res_id"])
        self.assertEqual(application.environment_id, self.environment)
        self.assertEqual(application.type_id, self.app_type)
        self.assertEqual(application.k8s_name, "status-page")
