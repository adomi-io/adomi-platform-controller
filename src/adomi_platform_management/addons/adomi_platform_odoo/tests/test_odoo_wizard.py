"""The Odoo product step extends the core launch wizard correctly."""

import json

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class _FakeGitHub:
    def __init__(self):
        self.generated = []

    def generate_from_template(self, template, name, owner=None, private=True, description=""):
        self.generated.append({"template": template, "name": name, "owner": owner})
        return {"html_url": "https://github.com/%s/%s" % (owner, name)}


class TestOdooWizard(TransactionCase):
    def setUp(self):
        super().setUp()
        no_push = self.env(context={"adomi_no_push": True, "adomi_config_no_push": True})
        self.client = no_push["adomi.client"].create({"name": "Acme", "k8s_name": "acme"})
        self.environment = no_push["adomi.environment"].create(
            {"name": "Prod", "k8s_name": "production", "client_id": self.client.id}
        )
        self.odoo_type = no_push["adomi.application.type"].search(
            [("k8s_name", "=", "odoo")], limit=1
        ) or no_push["adomi.application.type"].create({"name": "Odoo", "k8s_name": "odoo"})
        self.other_type = no_push["adomi.application.type"].create(
            {"name": "Uptime", "k8s_name": "uptime-kuma-test"}
        )

    def _wizard(self, type_rec, **extra):
        vals = {
            "client_id": self.client.id,
            "environment_id": self.environment.id,
            "type_id": type_rec.id,
            "app_name": "ERP",
        }
        vals.update(extra)
        return self.env["adomi.deploy.wizard"].create(vals)

    def test_odoo_step_injected_only_for_odoo(self):
        wiz = self._wizard(self.odoo_type)
        self.assertIn("odoo", wiz._visible_steps())

        other = self._wizard(self.other_type)
        self.assertNotIn("odoo", other._visible_steps())

    def test_edition_lands_in_application_values(self):
        wiz = self._wizard(self.odoo_type, odoo_edition="enterprise",
                           odoo_enterprise_repo="acme/enterprise", odoo_create_repo=False)
        vals = wiz._prepare_application_vals(self.client, self.environment)
        values = json.loads(vals["values"])
        self.assertEqual(values["odoo"]["edition"], "enterprise")
        self.assertEqual(values["odoo"]["enterpriseRepository"], "acme/enterprise")

    def test_enterprise_requires_enterprise_repo(self):
        wiz = self._wizard(self.odoo_type, odoo_edition="enterprise", odoo_create_repo=False)
        with self.assertRaises(UserError):
            wiz._validate_step("odoo")

    def test_after_launch_generates_boilerplate_repo(self):
        app_record = self.env["adomi.github.app"].create({"name": "Adomi"})
        installation = self.env["adomi.github.installation"].create(
            {"app_id": app_record.id, "installation_id": "1", "account_login": "acme-org"}
        )
        fake = _FakeGitHub()
        self.patch(type(installation), "_client", lambda s: fake)

        wiz = self._wizard(
            self.odoo_type,
            odoo_create_repo=True,
            odoo_github_installation_id=installation.id,
        )
        application = (
            self.env["adomi.application"]
            .with_context(adomi_no_push=True)
            .create(wiz._prepare_application_vals(self.client, self.environment))
        )
        wiz.with_context(adomi_no_push=True)._after_launch(application)

        self.assertEqual(fake.generated[0]["template"], "adomi-io/odoo-boilerplate")
        self.assertEqual(fake.generated[0]["owner"], "acme-org")
        self.assertEqual(fake.generated[0]["name"], "acme-odoo")
        self.assertTrue(application.git_repository_id)
        self.assertIn("github.com/acme-org/acme-odoo", application.git_repository_id.url)
