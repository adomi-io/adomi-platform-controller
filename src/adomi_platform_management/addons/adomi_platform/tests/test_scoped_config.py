"""Scoped Variables & Secrets: API dispatch per scope + the effective roll-up."""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class _RecordingApi:
    def __init__(self):
        self.upserts = []
        self.deletes = []

    def upsert(self, path, body):
        self.upserts.append({"path": path, "body": body})

    def delete(self, path):
        self.deletes.append({"path": path})


class TestScopedConfig(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("adomi_platform.write_backend", "api")
        self.api = _RecordingApi()
        self.patch(type(self.env["adomi.k8s.mixin"]), "_platform_api", lambda s: self.api)

        no_push = self.env(context={"adomi_no_push": True})
        self.org = no_push["adomi.organization"].create({"name": "Adomi", "k8s_name": "adomi-org"})
        self.client = no_push["adomi.client"].create(
            {"name": "Acme", "k8s_name": "acme", "organization_id": self.org.id}
        )
        self.environment = no_push["adomi.environment"].create(
            {"name": "Prod", "k8s_name": "production", "client_id": self.client.id}
        )
        app_type = no_push["adomi.application.type"].search([], limit=1) or no_push[
            "adomi.application.type"
        ].create({"name": "Odoo", "k8s_name": "odoo"})
        self.app = no_push["adomi.application"].create(
            {
                "name": "ERP",
                "k8s_name": "erp",
                "environment_id": self.environment.id,
                "type_id": app_type.id,
            }
        )

    def test_client_variable_dispatch(self):
        rec = self.env["adomi.scoped.config"].create(
            {"name": "TZ", "kind": "variable", "value": "UTC", "client_id": self.client.id}
        )
        call = self.api.upserts[-1]
        self.assertEqual(call["path"], "/v1/clients/acme/variables/TZ")
        self.assertEqual(call["body"], {"value": "UTC"})

        rec.unlink()
        self.assertEqual(self.api.deletes[-1]["path"], "/v1/clients/acme/variables/TZ")

    def test_environment_and_application_paths(self):
        self.env["adomi.scoped.config"].create(
            {"name": "TIER", "kind": "variable", "value": "prod", "environment_id": self.environment.id}
        )
        self.assertEqual(
            self.api.upserts[-1]["path"],
            "/v1/clients/acme/environments/production/variables/TIER",
        )
        self.env["adomi.scoped.config"].create(
            {"name": "PORT", "kind": "variable", "value": "8080", "application_id": self.app.id}
        )
        self.assertEqual(
            self.api.upserts[-1]["path"],
            "/v1/clients/acme/environments/production/applications/erp/variables/PORT",
        )

    def test_secret_value_never_persists(self):
        rec = self.env["adomi.scoped.config"].create(
            {
                "name": "API_KEY",
                "kind": "secret",
                "secret_value": "hunter2",
                "client_id": self.client.id,
            }
        )
        call = self.api.upserts[-1]
        self.assertEqual(call["path"], "/v1/clients/acme/secrets/API_KEY")
        self.assertEqual(call["body"], {"value": "hunter2"})
        self.assertTrue(rec.secret_set)
        self.assertFalse(rec.value)
        # Nothing secret in the database row.
        self.env.cr.execute("SELECT * FROM adomi_scoped_config WHERE id = %s", (rec.id,))
        row = self.env.cr.dictfetchone()
        self.assertNotIn("hunter2", str(row))

    def test_secret_requires_value(self):
        with self.assertRaises(UserError):
            self.env["adomi.scoped.config"].create(
                {"name": "EMPTY", "kind": "secret", "client_id": self.client.id}
            )

    def test_org_secret_uses_org_route(self):
        self.env["adomi.scoped.config"].create(
            {
                "name": "SMTP_PASS",
                "kind": "secret",
                "secret_value": "s",
                "organization_id": self.org.id,
            }
        )
        self.assertEqual(
            self.api.upserts[-1]["path"], "/v1/organizations/adomi-org/secrets/SMTP_PASS"
        )

    def test_org_variables_ride_the_organization_cr(self):
        self.env["adomi.scoped.config"].with_context(adomi_config_no_push=True).create(
            {"name": "TZ", "kind": "variable", "value": "UTC", "organization_id": self.org.id}
        )
        spec = self.org._k8s_spec()
        self.assertEqual(spec["variables"], [{"name": "TZ", "value": "UTC"}])

    def test_effective_config_roll_up(self):
        cfg = self.env["adomi.scoped.config"].with_context(adomi_config_no_push=True)
        cfg.create({"name": "TZ", "kind": "variable", "value": "UTC", "organization_id": self.org.id})
        cfg.create({"name": "TZ", "kind": "variable", "value": "EST", "client_id": self.client.id})
        cfg.create(
            {
                "name": "API_KEY",
                "kind": "secret",
                "secret_set": True,
                "application_id": self.app.id,
            }
        )

        entries = self.app.get_effective_config()
        by_key = {(e["name"], e["scope"]): e for e in entries}

        self.assertTrue(by_key[("TZ", "organization")]["overridden"])
        self.assertFalse(by_key[("TZ", "client")]["overridden"])
        self.assertEqual(by_key[("TZ", "client")]["value"], "EST")
        secret = by_key[("API_KEY", "application")]
        self.assertEqual(secret["kind"], "secret")
        self.assertFalse(secret["value"])  # names only, never values
