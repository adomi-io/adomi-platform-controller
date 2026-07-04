"""Reverse-sync matches CRs to records per client, not by bare k8s_name.

Every client has an environment named "production" and apps with catalog names
("superset", ...). A status push or import for one client's CR must never hit —
or merge into — another client's record with the same name.
"""

from odoo.tests.common import TransactionCase


def _cr(kind, name, namespace, spec=None, ready="True", message=""):
    return {
        "apiVersion": "platform.adomi.io/v1alpha1",
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec or {},
        "status": {"conditions": [{"type": "Ready", "status": ready, "message": message}]},
    }


class TestReverseSyncScoping(TransactionCase):
    def setUp(self):
        super().setUp()
        no_push = self.env(context={"adomi_no_push": True})
        self.acme = no_push["adomi.client"].create({"name": "Acme", "k8s_name": "acme"})
        self.globex = no_push["adomi.client"].create({"name": "Globex", "k8s_name": "globex"})
        self.acme_prod = no_push["adomi.environment"].create(
            {"name": "Production", "k8s_name": "production", "client_id": self.acme.id}
        )
        self.globex_prod = no_push["adomi.environment"].create(
            {"name": "Production", "k8s_name": "production", "client_id": self.globex.id}
        )
        self.app_type = no_push["adomi.application.type"].search([], limit=1) or no_push[
            "adomi.application.type"
        ].create({"name": "Odoo", "k8s_name": "odoo"})
        self.acme_app = no_push["adomi.application"].create(
            {
                "name": "Superset",
                "k8s_name": "superset",
                "environment_id": self.acme_prod.id,
                "type_id": self.app_type.id,
            }
        )
        self.globex_app = no_push["adomi.application"].create(
            {
                "name": "Superset",
                "k8s_name": "superset",
                "environment_id": self.globex_prod.id,
                "type_id": self.app_type.id,
            }
        )

    def test_environment_status_lands_on_the_right_client(self):
        obj = _cr(
            "Environment",
            "production",
            "adomi-client-globex",
            spec={"clientRef": {"name": "globex"}},
            ready="False",
            message="boom",
        )
        self.env["adomi.environment"].ingest_status("production", obj)
        self.assertEqual(self.globex_prod.k8s_state, "not_ready")
        self.assertNotEqual(self.acme_prod.k8s_state, "not_ready")

    def test_application_status_lands_on_the_right_client(self):
        obj = _cr(
            "Application",
            "superset",
            "adomi-client-globex",
            spec={"type": "odoo", "environmentRef": {"name": "production"}},
            ready="False",
            message="crash",
        )
        self.env["adomi.application"].ingest_status("superset", obj)
        self.assertEqual(self.globex_app.k8s_state, "not_ready")
        self.assertNotEqual(self.acme_app.k8s_state, "not_ready")

    def test_import_creates_instead_of_merging_across_clients(self):
        # A third client's "production" arrives from the cluster: it must become a
        # NEW record, not a status refresh of an existing client's environment.
        initech = self.env(context={"adomi_no_push": True})["adomi.client"].create(
            {"name": "Initech", "k8s_name": "initech"}
        )
        obj = _cr(
            "Environment",
            "production",
            "adomi-client-initech",
            spec={"clientRef": {"name": "initech"}, "class": "production"},
        )
        rec = self.env["adomi.environment"]._adomi_import_one(obj)
        self.assertTrue(rec)
        self.assertEqual(rec.client_id, initech)
        self.assertNotIn(rec, self.acme_prod | self.globex_prod)
        self.assertEqual(
            self.env["adomi.environment"].search_count([("k8s_name", "=", "production")]),
            3,
        )

    def test_application_import_resolves_environment_within_its_client(self):
        # Same-named app arriving for globex must attach to GLOBEX's production.
        self.globex_app.with_context(adomi_no_push=True).unlink()
        obj = _cr(
            "Application",
            "superset",
            "adomi-client-globex",
            spec={"type": self.app_type.k8s_name, "environmentRef": {"name": "production"}},
        )
        rec = self.env["adomi.application"]._adomi_import_one(obj)
        self.assertTrue(rec)
        self.assertNotEqual(rec, self.acme_app)
        self.assertEqual(rec.environment_id, self.globex_prod)

    def test_records_read_and_write_in_their_client_namespace(self):
        # Client intent lives in adomi-client-<slug>; the flat platform namespace
        # is only for platform-scoped resources. A wrong namespace here makes the
        # Sync button report "Not found in cluster" for perfectly healthy CRs.
        self.assertEqual(self.acme._k8s_ns(), "adomi-client-acme")
        self.assertEqual(self.globex_prod._k8s_ns(), "adomi-client-globex")
        self.assertEqual(self.acme_app._k8s_ns(), "adomi-client-acme")
        self.assertEqual(self.acme_app._k8s_body()["metadata"]["namespace"], "adomi-client-acme")
        # No client owner -> the flat platform namespace; cluster-scoped -> none.
        self.assertEqual(self.env["adomi.git.repository"]._k8s_ns(), "adomi-system")
        self.assertIsNone(self.env["adomi.organization"]._k8s_ns())

    def test_non_client_namespace_still_matches_by_name(self):
        # Platform-scoped / legacy single-namespace CRs keep the old behaviour.
        obj = _cr(
            "Environment",
            "production",
            "adomi-system",
            spec={"clientRef": {"name": "acme"}},
            ready="True",
            message="ok",
        )
        self.env["adomi.environment"].ingest_status("production", obj)
        self.assertEqual(self.acme_prod.k8s_state, "ready")
