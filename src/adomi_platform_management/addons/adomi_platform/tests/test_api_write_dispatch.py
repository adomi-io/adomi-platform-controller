"""The mixin routes writes to the platform API when the backend is 'api'."""

from odoo.tests.common import TransactionCase


class _RecordingApi:
    """Captures upsert/delete calls in place of a real PlatformApiClient."""

    def __init__(self):
        self.upserts = []
        self.deletes = []

    def upsert(self, path, body):
        self.upserts.append({"path": path, "body": body})

    def delete(self, path):
        self.deletes.append({"path": path})


class TestApiWriteDispatch(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("adomi_platform.write_backend", "api")
        self.api = _RecordingApi()
        self.patch(type(self.env["adomi.k8s.mixin"]), "_platform_api", lambda s: self.api)

    def _new_client(self):
        return self.env["adomi.client"].create({"name": "Acme", "k8s_name": "acme"})

    def test_create_client_calls_api(self):
        self._new_client()
        self.assertEqual(len(self.api.upserts), 1)
        call = self.api.upserts[0]
        self.assertEqual(call["path"], "/v1/clients/acme")  # the client IS the resource
        self.assertEqual(call["body"], {"display_name": "Acme"})

    def test_environment_routes_to_client_client(self):
        client = self._new_client()
        self.api.upserts.clear()
        self.env["adomi.environment"].create(
            {"name": "Dev", "k8s_name": "dev", "client_id": client.id, "environment_class": "development"}
        )
        call = self.api.upserts[-1]
        self.assertEqual(call["path"], "/v1/clients/acme/environments/dev")
        self.assertEqual(call["body"], {"display_name": "Dev", "class": "development"})

    def test_application_nests_under_environment(self):
        client = self._new_client()
        environment = self.env["adomi.environment"].create(
            {"name": "Prod", "k8s_name": "prod", "client_id": client.id, "environment_class": "production"}
        )
        app_type = self.env["adomi.application.type"].search([], limit=1)
        if not app_type:
            app_type = self.env["adomi.application.type"].with_context(adomi_no_push=True).create(
                {"name": "Odoo", "k8s_name": "odoo"}
            )
        self.api.upserts.clear()
        self.env["adomi.application"].create(
            {
                "name": "ERP",
                "k8s_name": "erp",
                "environment_id": environment.id,
                "type_id": app_type.id,
                "hostname": "erp.acme.example.com",
                "database_ids": [
                    (0, 0, {"name": "erp", "server_name": "acme-prod-db", "secret": "erp-db"})
                ],
            }
        )
        call = self.api.upserts[-1]
        self.assertEqual(call["path"], "/v1/clients/acme/environments/prod/applications/erp")
        self.assertEqual(call["body"]["type"], "odoo")
        self.assertEqual(call["body"]["host"], "erp.acme.example.com")
        self.assertEqual(call["body"]["databases"][0]["server"], "acme-prod-db")
        self.assertEqual(call["body"]["databases"][0]["credentials"], {"secret": "erp-db"})

    def test_git_repository_routes_to_client_repo(self):
        # Customer-scoped: the GitRepository CR must be committed to the
        # customer's infrastructure repo (client namespace), where that
        # customer's applications resolve their sourceRepositoryRef — NOT to
        # the shared platform namespace.
        client = self._new_client()
        self.api.upserts.clear()
        self.env["adomi.git.repository"].create(
            {
                "name": "acme/acme-odoo",
                "k8s_name": "acme-odoo",
                "client_id": client.id,
                "url": "https://github.com/acme/acme-odoo",
                "default_branch": "main",
            }
        )
        call = self.api.upserts[-1]
        self.assertEqual(call["path"], "/v1/clients/acme/gitrepositories/acme-odoo")
        self.assertEqual(
            call["body"],
            {"url": "https://github.com/acme/acme-odoo", "default_branch": "main"},
        )

    def test_unlink_calls_delete(self):
        self._new_client().unlink()
        self.assertTrue(any(d["path"] == "/v1/clients/acme" for d in self.api.deletes))

    def test_cluster_scoped_stays_on_kubernetes(self):
        # Organization is cluster-scoped / platform-owned: never routed to the API.
        self.assertFalse(self.env["adomi.organization"]._k8s_client_slug())

    def test_kubernetes_backend_does_not_call_api(self):
        self.env["ir.config_parameter"].sudo().set_param("adomi_platform.write_backend", "kubernetes")
        self._new_client()
        self.assertEqual(self.api.upserts, [])

    def test_missing_cr_is_pending_on_api_backend(self):
        # After a write the CR is committed to git but GitOps hasn't applied it
        # yet: that's provisioning in flight, not "Not found in cluster".
        from odoo.addons.adomi_platform.models import k8s as k8s_mod

        self.patch(k8s_mod, "get", lambda *a, **kw: None)
        client = self._new_client()
        client.action_k8s_sync()
        self.assertEqual(client.k8s_state, "pending")
        self.assertIn("Committed to the client repo", client.k8s_message)

    def test_missing_cr_is_unknown_for_platform_records(self):
        # Platform-scoped records (no client owner) keep the hard "not found".
        from odoo.addons.adomi_platform.models import k8s as k8s_mod

        self.patch(k8s_mod, "get", lambda *a, **kw: None)
        org = self.env["adomi.organization"].with_context(adomi_no_push=True).create(
            {"name": "Adomi", "k8s_name": "adomi"}
        )
        org.action_k8s_sync()
        self.assertEqual(org.k8s_state, "unknown")
        self.assertIn("Not found in cluster", org.k8s_message)
