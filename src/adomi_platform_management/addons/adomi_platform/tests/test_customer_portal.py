"""The customer page as THE portal: domains, host wiring, the portal payload."""

from odoo.tests.common import TransactionCase


class PortalCase(TransactionCase):
    def setUp(self):
        super().setUp()
        self.no_push = self.env(context={"adomi_no_push": True, "adomi_config_no_push": True})
        self.org = self.no_push["adomi.organization"].create(
            {"name": "Adomi", "k8s_name": "adomi", "base_domain": "adomi.app"}
        )
        self.client = self.no_push["adomi.client"].create(
            {"name": "Acme", "k8s_name": "acme", "organization_id": self.org.id}
        )
        self.environment = self.no_push["adomi.environment"].create(
            {
                "name": "production",
                "k8s_name": "production",
                "client_id": self.client.id,
                "environment_class": "production",
            }
        )
        self.app_type = self.no_push["adomi.application.type"].create(
            {"name": "Odoo", "k8s_name": "odoo-portal"}
        )


class TestDomain(PortalCase):
    def test_create_defaults_name_and_resource_name_from_fqdn(self):
        domain = self.no_push["adomi.domain"].create(
            {"client_id": self.client.id, "fqdn": "acme.com"}
        )
        self.assertEqual(domain.name, "acme.com")
        self.assertEqual(domain.k8s_name, "acme-com")

    def test_api_body_and_spec(self):
        domain = self.no_push["adomi.domain"].create(
            {"client_id": self.client.id, "fqdn": "Acme.COM ", "issuer": "letsencrypt-dns"}
        )
        self.assertEqual(
            domain._api_body(),
            {"fqdn": "acme.com", "wildcard": True, "issuer": "letsencrypt-dns"},
        )
        self.assertEqual(
            domain._k8s_spec(),
            {"fqdn": "acme.com", "wildcard": True, "issuer": "letsencrypt-dns"},
        )
        self.assertEqual(domain._api_path(), "/v1/clients/acme/domains/acme-com")

    def test_platform_mode_derives_fqdn_under_org_domain(self):
        domain = self.no_push["adomi.domain"].new(
            {"client_id": self.client.id, "mode": "platform", "platform_label": "Acme"}
        )
        domain._onchange_platform_fqdn()
        self.assertEqual(domain.fqdn, "acme.adomi.app")

    def test_cname_target_falls_back_to_org_base_domain(self):
        domain = self.no_push["adomi.domain"].create(
            {"client_id": self.client.id, "fqdn": "acme.com"}
        )
        self.assertEqual(domain.cname_target, "adomi.app")
        self.env["ir.config_parameter"].sudo().set_param(
            "adomi_platform.edge_host", "edge.adomi.app"
        )
        domain.invalidate_recordset(["cname_target"])
        self.assertEqual(domain.cname_target, "edge.adomi.app")

    def test_import_detects_mode(self):
        Domain = self.no_push["adomi.domain"]
        obj = {
            "metadata": {"name": "acme-com", "namespace": "adomi-client-acme"},
            "spec": {"fqdn": "acme.com", "wildcard": False},
        }
        vals = Domain._k8s_import_vals(obj)
        self.assertEqual(vals["mode"], "byo")
        self.assertEqual(vals["client_id"], self.client.id)
        self.assertFalse(vals["wildcard"])

        obj["spec"]["fqdn"] = "acme.adomi.app"
        vals = Domain._k8s_import_vals(obj)
        self.assertEqual(vals["mode"], "platform")
        self.assertEqual(vals["platform_label"], "acme")


class TestApplicationHost(PortalCase):
    def _app(self, **extra):
        vals = {
            "name": "erp",
            "k8s_name": "erp",
            "environment_id": self.environment.id,
            "type_id": self.app_type.id,
        }
        vals.update(extra)
        return self.no_push["adomi.application"].create(vals)

    def test_subdomain_plus_domain_composes_host(self):
        domain = self.no_push["adomi.domain"].create(
            {"client_id": self.client.id, "fqdn": "acme.com"}
        )
        app = self._app(domain_id=domain.id, subdomain="ERP")
        self.assertEqual(app._composed_host(), "erp.acme.com")
        self.assertEqual(app.host_effective, "erp.acme.com")

        body = app._api_body()
        self.assertEqual(body["host"], "erp.acme.com")
        self.assertEqual(body["domain"], "acme-com")

        spec = app._k8s_spec()
        self.assertEqual(spec["ingress"], {"host": "erp.acme.com"})
        self.assertEqual(spec["domainRef"], {"name": "acme-com"})

    def test_hostname_override_wins(self):
        domain = self.no_push["adomi.domain"].create(
            {"client_id": self.client.id, "fqdn": "acme.com"}
        )
        app = self._app(domain_id=domain.id, subdomain="erp", hostname="legacy.acme.net")
        self.assertEqual(app._composed_host(), "legacy.acme.net")
        self.assertEqual(app._api_body()["host"], "legacy.acme.net")

    def test_no_domain_generates_nothing(self):
        app = self._app()
        self.assertFalse(app._composed_host())
        self.assertNotIn("host", app._api_body())
        self.assertNotIn("domain", app._api_body())
        self.assertNotIn("ingress", app._k8s_spec())

    def test_import_splits_host_under_domain(self):
        domain = self.no_push["adomi.domain"].create(
            {"client_id": self.client.id, "fqdn": "acme.com"}
        )
        obj = {
            "metadata": {"name": "erp2", "namespace": "adomi-client-acme"},
            "spec": {
                "environmentRef": {"name": "production"},
                "type": "odoo-portal",
                "domainRef": {"name": "acme-com"},
                "ingress": {"host": "erp.acme.com"},
            },
        }
        vals = self.no_push["adomi.application"]._k8s_import_vals(obj)
        self.assertEqual(vals["domain_id"], domain.id)
        self.assertEqual(vals["subdomain"], "erp")
        self.assertFalse(vals["hostname"])

    def test_import_keeps_foreign_host_as_override(self):
        obj = {
            "metadata": {"name": "erp3", "namespace": "adomi-client-acme"},
            "spec": {
                "environmentRef": {"name": "production"},
                "type": "odoo-portal",
                "ingress": {"host": "legacy.acme.net"},
            },
        }
        vals = self.no_push["adomi.application"]._k8s_import_vals(obj)
        self.assertFalse(vals["domain_id"])
        self.assertFalse(vals["subdomain"])
        self.assertEqual(vals["hostname"], "legacy.acme.net")


class TestPortalData(PortalCase):
    def test_portal_payload(self):
        domain = self.no_push["adomi.domain"].create(
            {"client_id": self.client.id, "fqdn": "acme.com"}
        )
        server = self.no_push["adomi.database.server"].create(
            {
                "name": "acme-pg",
                "k8s_name": "acme-pg",
                "client_id": self.client.id,
                "environment_id": self.environment.id,
            }
        )
        dev = self.no_push["adomi.environment"].create(
            {"name": "development", "k8s_name": "development", "client_id": self.client.id}
        )
        app = self.no_push["adomi.application"].create(
            {
                "name": "erp",
                "k8s_name": "erp",
                "environment_id": self.environment.id,
                "type_id": self.app_type.id,
                "domain_id": domain.id,
                "subdomain": "erp",
                "database_ids": [
                    (0, 0, {"name": "main", "server_id": server.id, "secret": "erp-db"})
                ],
            }
        )
        self.no_push["adomi.scoped.config"].create(
            {"name": "COMPANY_NAME", "kind": "variable", "value": "Acme", "client_id": self.client.id}
        )

        data = self.client.get_portal_data()

        self.assertEqual(data["client"]["slug"], "acme")
        self.assertEqual(data["client"]["base_domain"], "adomi.app")

        self.assertEqual([d["fqdn"] for d in data["domains"]], ["acme.com"])
        self.assertEqual(data["domains"][0]["cname_target"], "adomi.app")
        self.assertEqual(data["domains"][0]["app_count"], 1)

        self.assertEqual([s["name"] for s in data["servers"]], ["acme-pg"])

        # production sorts before development; the env-scoped server shows only there
        names = [e["name"] for e in data["environments"]]
        self.assertEqual(names, ["production", "development"])
        prod, devl = data["environments"]
        self.assertEqual([s["name"] for s in prod["servers"]], ["acme-pg"])
        self.assertEqual(devl["servers"], [])

        self.assertEqual(len(prod["apps"]), 1)
        app_data = prod["apps"][0]
        self.assertEqual(app_data["id"], app.id)
        self.assertEqual(app_data["host"]["effective"], "erp.acme.com")
        self.assertEqual(app_data["host"]["domain"], "acme.com")
        self.assertEqual(app_data["databases"][0]["server"], "acme-pg")
        self.assertIn("COMPANY_NAME", [c["name"] for c in app_data["config"]])
