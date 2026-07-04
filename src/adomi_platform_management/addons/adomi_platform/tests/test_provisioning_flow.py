"""The customer form's provisioning journey derives correctly from sync state."""

from odoo.tests.common import TransactionCase


class TestProvisioningFlow(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "adomi_platform.git_public_base", "https://git.example.com/clients"
        )
        self.client = (
            self.env["adomi.client"]
            .with_context(adomi_no_push=True)
            .create({"name": "Acme", "k8s_name": "acme"})
        )

    def _set(self, state, message=""):
        self.client.with_context(adomi_no_push=True).write(
            {"k8s_state": state, "k8s_message": message}
        )

    def test_repo_deep_link(self):
        self.assertEqual(self.client.infra_repo_url, "https://git.example.com/clients/acme")

    def test_no_base_no_link(self):
        self.env["ir.config_parameter"].sudo().set_param("adomi_platform.git_public_base", "")
        self.client.invalidate_recordset()
        self.assertFalse(self.client.infra_repo_url)

    def test_committed_while_gitops_in_flight(self):
        self._set("pending", "Committed to the client repo; waiting for the platform to apply it.")
        self.assertEqual(self.client.provisioning_stage, "committed")

    def test_applied_once_cr_exists(self):
        self._set("pending", "Client 'acme' reconciling")
        self.assertEqual(self.client.provisioning_stage, "applied")

    def test_ready(self):
        self._set("ready", "Client 'acme' reconciled")
        self.assertEqual(self.client.provisioning_stage, "ready")

    def test_failed(self):
        self._set("unknown", "Sync failed: boom")
        self.assertEqual(self.client.provisioning_stage, "failed")
        self._set("not_ready", "Client 'acme' degraded")
        self.assertEqual(self.client.provisioning_stage, "failed")
