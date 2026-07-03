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

    def test_workspace_routes_to_client_tenant(self):
        client = self._new_client()
        self.api.upserts.clear()
        self.env["adomi.workspace"].create(
            {"name": "Dev", "k8s_name": "dev", "client_id": client.id, "workspace_class": "development"}
        )
        call = self.api.upserts[-1]
        self.assertEqual(call["path"], "/v1/clients/acme/workspaces/dev")
        self.assertEqual(call["body"], {"display_name": "Dev", "class": "development"})

    def test_application_nests_under_workspace(self):
        client = self._new_client()
        workspace = self.env["adomi.workspace"].create(
            {"name": "Prod", "k8s_name": "prod", "client_id": client.id, "workspace_class": "production"}
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
                "workspace_id": workspace.id,
                "type_id": app_type.id,
                "hostname": "erp.acme.example.com",
                "database_ids": [
                    (0, 0, {"name": "erp", "server_name": "acme-prod-db", "secret": "erp-db"})
                ],
            }
        )
        call = self.api.upserts[-1]
        self.assertEqual(call["path"], "/v1/clients/acme/workspaces/prod/applications/erp")
        self.assertEqual(call["body"]["type"], "odoo")
        self.assertEqual(call["body"]["host"], "erp.acme.example.com")
        self.assertEqual(call["body"]["databases"][0]["server"], "acme-prod-db")
        self.assertEqual(call["body"]["databases"][0]["credentials"], {"secret": "erp-db"})

    def test_unlink_calls_delete(self):
        self._new_client().unlink()
        self.assertTrue(any(d["path"] == "/v1/clients/acme" for d in self.api.deletes))

    def test_cluster_scoped_stays_on_kubernetes(self):
        # Organization is cluster-scoped / platform-owned: never routed to the API.
        self.assertFalse(self.env["adomi.organization"]._k8s_tenant_slug())

    def test_kubernetes_backend_does_not_call_api(self):
        self.env["ir.config_parameter"].sudo().set_param("adomi_platform.write_backend", "kubernetes")
        self._new_client()
        self.assertEqual(self.api.upserts, [])
