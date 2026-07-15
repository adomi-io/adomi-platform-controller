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
                           odoo_enterprise_repo="acme/enterprise", odoo_repo_mode="none")
        vals = wiz._prepare_application_vals(self.client, self.environment)
        values = json.loads(vals["values"])
        self.assertEqual(values["odoo"]["edition"], "enterprise")
        self.assertEqual(values["odoo"]["enterpriseRepository"], "acme/enterprise")

    def test_enterprise_requires_enterprise_repo(self):
        wiz = self._wizard(self.odoo_type, odoo_edition="enterprise", odoo_repo_mode="none")
        with self.assertRaises(UserError):
            wiz._validate_step("odoo")

    def _installation(self):
        app_record = self.env["adomi.github.app"].create({"name": "Adomi"})
        return self.env["adomi.github.installation"].create(
            {"app_id": app_record.id, "installation_id": "1", "account_login": "acme-org"}
        )

    def test_after_launch_generates_boilerplate_repo(self):
        installation = self._installation()
        fake = _FakeGitHub()
        self.patch(type(installation), "_client", lambda s: fake)

        wiz = self._wizard(
            self.odoo_type,
            odoo_repo_mode="generate",
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
        # Customer-scoped, so the CR lands in the customer's infrastructure
        # repo where the application resolves it.
        self.assertEqual(application.git_repository_id.client_id, self.client)

        project = self.env["adomi.odoo.project"].search(
            [("application_id", "=", application.id)]
        )
        self.assertEqual(len(project), 1)
        self.assertEqual(project.client_id, self.client)
        self.assertEqual(project.edition, "community")
        self.assertEqual(project.git_repository_id, application.git_repository_id)

    def test_after_launch_links_existing_repo(self):
        installation = self._installation()
        fake = _FakeGitHub()
        self.patch(type(installation), "_client", lambda s: fake)
        repo = self.env["adomi.github.repository"].create(
            {
                "installation_id": installation.id,
                "name": "acme-erp",
                "full_name": "acme-org/acme-erp",
                "html_url": "https://github.com/acme-org/acme-erp",
                "default_branch": "main",
            }
        )

        wiz = self._wizard(
            self.odoo_type,
            odoo_repo_mode="existing",
            odoo_github_installation_id=installation.id,
            odoo_existing_repo_id=repo.id,
        )
        wiz._validate_step("odoo")
        application = (
            self.env["adomi.application"]
            .with_context(adomi_no_push=True)
            .create(wiz._prepare_application_vals(self.client, self.environment))
        )
        wiz.with_context(adomi_no_push=True)._after_launch(application)

        self.assertFalse(fake.generated)  # nothing generated: the repo already exists
        self.assertTrue(application.git_repository_id)
        self.assertEqual(
            application.git_repository_id.url, "https://github.com/acme-org/acme-erp"
        )
        self.assertEqual(application.git_repository_id.client_id, self.client)
        self.assertTrue(
            self.env["adomi.odoo.project"].search(
                [("application_id", "=", application.id)]
            )
        )

    def test_existing_mode_requires_a_repo(self):
        installation = self._installation()
        wiz = self._wizard(
            self.odoo_type,
            odoo_repo_mode="existing",
            odoo_github_installation_id=installation.id,
        )
        with self.assertRaises(UserError):
            wiz._validate_step("odoo")

    def test_database_line_wires_odoo_db_connect_env(self):
        """The wizard must emit the explicit ODOO_DB_* connect env for the
        database it provisions — the odoo chart wires nothing by itself, so an
        Application without these env lines deploys with no database at all."""
        self.odoo_type.database_required = True
        server = self.env(context={"adomi_no_push": True})["adomi.database.server"].create(
            {"name": "acme-prod-db", "client_id": self.client.id}
        )
        wiz = self._wizard(
            self.odoo_type, odoo_repo_mode="none", database_server_id=server.id
        )

        vals = wiz._prepare_application_vals(self.client, self.environment)

        secret = vals["database_ids"][0][2]["secret"]
        env = {line[2]["name"]: line[2] for line in vals["env_ids"]}
        expected = {
            "ODOO_DB_HOST": "host",
            "ODOO_DB_PORT": "port",
            "ODOO_DB_USER": "user",
            "ODOO_DB_NAME": "dbname",
            "ODOO_DB_PASSWORD": "password",
        }
        self.assertEqual({n: e["secret_key"] for n, e in env.items()}, expected)
        for line in env.values():
            self.assertEqual(line["secret_name"], secret)

    def test_no_database_line_means_no_db_env(self):
        wiz = self._wizard(self.odoo_type, odoo_repo_mode="none")
        vals = wiz._prepare_application_vals(self.client, self.environment)
        self.assertNotIn("env_ids", vals)
