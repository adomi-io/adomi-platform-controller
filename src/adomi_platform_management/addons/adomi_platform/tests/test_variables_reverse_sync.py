"""spec.variables set in git/cluster are picked up as portal Variable records."""

from odoo.tests.common import TransactionCase


class TestVariablesReverseSync(TransactionCase):
    def setUp(self):
        super().setUp()
        no_push = self.env(context={"adomi_no_push": True, "adomi_config_no_push": True})
        self.client = no_push["adomi.client"].create({"name": "Acme", "k8s_name": "acme"})
        self.environment = no_push["adomi.environment"].create(
            {"name": "Prod", "k8s_name": "production", "client_id": self.client.id}
        )
        self.app_type = no_push["adomi.application.type"].create(
            {"name": "Uptime", "k8s_name": "uptime-kuma-sync"}
        )
        self.application = no_push["adomi.application"].create(
            {
                "name": "Status",
                "k8s_name": "status",
                "environment_id": self.environment.id,
                "type_id": self.app_type.id,
            }
        )

    def _backdate(self, rec, hours=1):
        # Flush first so a later ORM flush can't stamp write_date back to now.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE adomi_scoped_config SET write_date = write_date - %s * interval '1 hour' "
            "WHERE id = %s",
            (hours, rec.id),
        )
        self.env.invalidate_all()

    def _vars(self, owner):
        return owner.scoped_config_ids.filtered(lambda c: c.kind == "variable")

    def test_cr_variables_create_portal_records(self):
        self.application._adomi_sync_variables(
            {"spec": {"variables": [{"name": "SMTP_HOST", "value": "mail.acme.com"}]}}
        )
        rec = self._vars(self.application)
        self.assertEqual(rec.name, "SMTP_HOST")
        self.assertEqual(rec.value, "mail.acme.com")
        self.assertEqual(rec.application_id, self.application)

    def test_sync_runs_on_status_apply(self):
        # The ingest/sync path (controller status push) carries the full CR.
        self.application._k8s_apply_obj(
            {
                "spec": {"variables": [{"name": "TZ", "value": "UTC"}]},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        )
        self.assertEqual(self._vars(self.application).name, "TZ")

    def test_fresh_portal_edits_survive_a_stale_cr(self):
        config = (
            self.env["adomi.scoped.config"]
            .with_context(adomi_config_no_push=True)
            .create(
                {
                    "name": "TZ",
                    "kind": "variable",
                    "value": "EST",
                    "application_id": self.application.id,
                }
            )
        )
        # A stale CR (older value, or missing the variable) must not clobber it.
        self.application._adomi_sync_variables(
            {"spec": {"variables": [{"name": "TZ", "value": "UTC"}]}}
        )
        self.assertEqual(config.value, "EST")
        self.application._adomi_sync_variables({"spec": {}})
        self.assertTrue(config.exists())

    def test_settled_records_follow_the_cr(self):
        config = (
            self.env["adomi.scoped.config"]
            .with_context(adomi_config_no_push=True)
            .create(
                {
                    "name": "TZ",
                    "kind": "variable",
                    "value": "EST",
                    "application_id": self.application.id,
                }
            )
        )
        self._backdate(config)
        self.application._adomi_sync_variables(
            {"spec": {"variables": [{"name": "TZ", "value": "UTC"}]}}
        )
        self.assertEqual(config.value, "UTC")

        self._backdate(config)
        self.application._adomi_sync_variables({"spec": {}})
        self.assertFalse(config.exists())

    def test_environment_scope_syncs_too(self):
        self.environment._adomi_sync_variables(
            {"spec": {"variables": [{"name": "REGION", "value": "us-east"}]}}
        )
        rec = self._vars(self.environment)
        self.assertEqual(rec.environment_id, self.environment)
        self.assertEqual(rec.value, "us-east")


class TestAppScopedObservability(TransactionCase):
    def setUp(self):
        super().setUp()
        no_push = self.env(context={"adomi_no_push": True, "adomi_config_no_push": True})
        client = no_push["adomi.client"].create({"name": "Acme", "k8s_name": "acme"})
        environment = no_push["adomi.environment"].create(
            {"name": "Prod", "k8s_name": "production", "client_id": client.id}
        )
        app_type = no_push["adomi.application.type"].create(
            {"name": "Uptime", "k8s_name": "uptime-kuma-obs"}
        )
        self.application = no_push["adomi.application"].create(
            {
                "name": "Status",
                "k8s_name": "status",
                "environment_id": environment.id,
                "type_id": app_type.id,
            }
        )
        self.application.with_context(adomi_no_push=True).write(
            {"namespace": "acme-production"}
        )
        self.environment = environment

    def test_application_queries_are_pod_scoped(self):
        # The release is <namespace>-<app>; its pods are acme-production-status-*.
        self.assertEqual(
            self.application._obs_label_filters(),
            'namespace="acme-production",pod=~"acme-production-status-.*"',
        )
        self.assertEqual(
            self.application._obs_log_query(),
            '{namespace="acme-production",pod=~"acme-production-status-.*"}',
        )

    def test_environment_queries_stay_namespace_wide(self):
        self.environment.with_context(adomi_no_push=True).write(
            {"namespace": "acme-production"}
        )
        self.assertEqual(
            self.environment._obs_label_filters(),
            'namespace="acme-production"',
        )

    def test_search_is_escaped_into_the_line_filter(self):
        query = self.application._obs_log_query('error "500"')
        self.assertEqual(
            query,
            '{namespace="acme-production",pod=~"acme-production-status-.*"}'
            ' |= "error \\"500\\""',
        )
