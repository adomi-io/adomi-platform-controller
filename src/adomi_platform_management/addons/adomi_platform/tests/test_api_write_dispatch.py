"""The mixin routes writes to the platform API when the backend is 'api'."""

from odoo.tests.common import TransactionCase


class _RecordingApi:
    """Captures upsert/delete calls in place of a real PlatformApiClient."""

    def __init__(self):
        self.upserts = []
        self.deletes = []

    def upsert(self, client, plural, name, spec, labels=None):
        self.upserts.append({"client": client, "plural": plural, "name": name, "spec": spec})

    def delete(self, client, plural, name):
        self.deletes.append({"client": client, "plural": plural, "name": name})


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
        self.assertEqual(call["client"], "acme")     # tenant == client slug
        self.assertEqual(call["plural"], "clients")
        self.assertEqual(call["name"], "acme")
        self.assertEqual(call["spec"]["displayName"], "Acme")

    def test_workspace_routes_to_client_tenant(self):
        client = self._new_client()
        self.api.upserts.clear()
        self.env["adomi.workspace"].create(
            {"name": "Dev", "k8s_name": "dev", "client_id": client.id, "workspace_class": "development"}
        )
        call = self.api.upserts[-1]
        self.assertEqual(call["client"], "acme")
        self.assertEqual(call["plural"], "workspaces")

    def test_unlink_calls_delete(self):
        self._new_client().unlink()
        self.assertTrue(any(d["plural"] == "clients" and d["name"] == "acme" for d in self.api.deletes))

    def test_cluster_scoped_stays_on_kubernetes(self):
        # Organization is cluster-scoped / platform-owned: never routed to the API.
        self.assertFalse(self.env["adomi.organization"]._k8s_tenant_slug())

    def test_kubernetes_backend_does_not_call_api(self):
        self.env["ir.config_parameter"].sudo().set_param("adomi_platform.write_backend", "kubernetes")
        self._new_client()
        self.assertEqual(self.api.upserts, [])
