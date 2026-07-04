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
