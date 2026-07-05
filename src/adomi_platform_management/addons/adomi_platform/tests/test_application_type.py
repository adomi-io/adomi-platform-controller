"""The catalog card content round-trips between the CR spec and the Odoo model."""

from odoo.tests.common import TransactionCase


class TestApplicationTypeCatalog(TransactionCase):
    def _cr(self, catalog=None):
        spec = {
            "displayName": "Odoo",
            "chart": {"repoURL": "https://example.com/charts.git", "path": "odoo"},
            "database": {"required": True},
            "sso": {"enabled": True, "protocol": "proxy"},
        }

        if catalog is not None:
            spec["catalog"] = catalog

        return {"metadata": {"name": "odoo"}, "spec": spec}

    def test_import_reads_catalog_block(self):
        vals = self.env["adomi.application.type"]._k8s_import_vals(
            self._cr(
                {
                    "description": "All-in-one business software.",
                    "about": "CRM, accounting and more.",
                    "icon": "fa-cubes",
                    "logoUrl": "https://cdn.example.com/odoo.png",
                    "images": [
                        "https://cdn.example.com/shot-1.png",
                        "https://cdn.example.com/shot-2.png",
                    ],
                    "category": "ERP",
                    "vendor": "Odoo S.A.",
                    "websiteUrl": "https://www.odoo.com",
                }
            )
        )

        self.assertEqual(vals["description"], "All-in-one business software.")
        self.assertEqual(vals["icon"], "fa-cubes")
        self.assertEqual(vals["logo_url"], "https://cdn.example.com/odoo.png")
        self.assertEqual(
            vals["image_urls"],
            "https://cdn.example.com/shot-1.png\nhttps://cdn.example.com/shot-2.png",
        )
        self.assertEqual(vals["category"], "ERP")
        self.assertEqual(vals["vendor"], "Odoo S.A.")
        self.assertEqual(vals["website_url"], "https://www.odoo.com")

    def test_import_without_catalog_clears_fields(self):
        vals = self.env["adomi.application.type"]._k8s_import_vals(self._cr())

        for key in ("description", "icon", "logo_url", "image_urls", "category", "vendor"):
            self.assertFalse(vals[key])

    def test_spec_emits_catalog_only_when_set(self):
        rec = (
            self.env["adomi.application.type"]
            .with_context(adomi_no_push=True)
            .create(
                {
                    "name": "Odoo",
                    "k8s_name": "odoo",
                    "chart_repo_url": "https://example.com/charts.git",
                    "chart_path": "odoo",
                }
            )
        )
        self.assertNotIn("catalog", rec._k8s_spec())

        rec.with_context(adomi_no_push=True).write(
            {
                "description": "All-in-one business software.",
                "category": "ERP",
                "image_urls": "https://cdn.example.com/shot-1.png\n\n",
            }
        )
        catalog = rec._k8s_spec()["catalog"]
        self.assertEqual(catalog["description"], "All-in-one business software.")
        self.assertEqual(catalog["category"], "ERP")
        self.assertEqual(catalog["images"], ["https://cdn.example.com/shot-1.png"])
        self.assertNotIn("icon", catalog)

    def test_roundtrip_import_then_spec(self):
        catalog = {
            "description": "Uptime monitoring with status pages.",
            "icon": "fa-heartbeat",
            "category": "Monitoring",
            "vendor": "Uptime Kuma",
            "websiteUrl": "https://uptime.kuma.pet",
        }
        vals = self.env["adomi.application.type"]._k8s_import_vals(
            self._cr(catalog)
        )
        rec = (
            self.env["adomi.application.type"]
            .with_context(adomi_no_push=True)
            .create(dict(vals, k8s_name="odoo"))
        )

        self.assertEqual(rec._k8s_spec()["catalog"], catalog)
