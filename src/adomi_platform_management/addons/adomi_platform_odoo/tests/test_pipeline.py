"""Pipeline content: manifest/Dockerfile generation + the commit/import actions."""

import unittest

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from odoo.addons.adomi_platform_odoo.models import pipeline

SAMPLE = {
    "base_image": "ghcr.io/adomi-io/odoo:19.0",
    "apt": ["libxmlsec1-dev"],
    "pip": ["stripe", "python-slugify"],
    "addons": [
        {
            "repo": "https://github.com/OCA/web.git",
            "branch": "19.0",
            "modules": ["web_responsive"],
        },
        {"repo": "https://github.com/OCA/server-brand.git", "branch": "19.0", "modules": []},
    ],
}


class TestRenderers(unittest.TestCase):
    def test_manifest_roundtrip(self):
        text = pipeline.render_manifest(SAMPLE)

        self.assertIn("odooBaseImage: ghcr.io/adomi-io/odoo:19.0", text)
        self.assertEqual(pipeline.parse_manifest(text), SAMPLE)

    def test_parse_tolerates_missing_keys(self):
        parsed = pipeline.parse_manifest("odooBaseImage: x\n")

        self.assertEqual(parsed["base_image"], "x")
        self.assertEqual(parsed["addons"], [])
        # Entries without a repo are dropped rather than generating broken stages.
        parsed = pipeline.parse_manifest("addons:\n  - branch: '19.0'\n")
        self.assertEqual(parsed["addons"], [])

    def test_dockerfile_structure(self):
        text = pipeline.render_dockerfile(SAMPLE)

        self.assertIn("ARG ODOO_BASE_IMAGE=ghcr.io/adomi-io/odoo:19.0", text)
        # One clone stage per source; explicit modules copied one by one, an
        # empty list takes every addon the repo ships.
        self.assertIn(
            "RUN git clone --depth 1 --branch 19.0 https://github.com/OCA/web.git /tmp/src",
            text,
        )
        self.assertIn("cp -a /tmp/src/web_responsive /tmp/extra_addons/", text)
        self.assertIn('for m in /tmp/src/*/__manifest__.py; do cp -a "$(dirname "$m")"', text)
        # Dependency layers.
        self.assertIn("apt-get install -y --no-install-recommends", text)
        self.assertIn("        libxmlsec1-dev", text)
        self.assertIn("RUN pip install", text)
        self.assertIn("        stripe", text)
        # The boilerplate contract: config + repo addons + staged extras land in /volumes.
        self.assertIn("COPY config/odoo.conf /volumes/config/odoo.conf", text)
        self.assertIn("COPY addons /volumes/addons", text)
        self.assertIn("COPY --from=addons_0_web /tmp/extra_addons/ /volumes/extra_addons/", text)
        self.assertIn(
            "COPY --from=addons_1_server_brand /tmp/extra_addons/ /volumes/extra_addons/", text
        )

    def test_dockerfile_without_sources_or_deps_stays_minimal(self):
        text = pipeline.render_dockerfile({})

        self.assertIn("ARG ODOO_BASE_IMAGE=%s" % pipeline.DEFAULT_BASE_IMAGE, text)
        self.assertNotIn("git clone", text)
        self.assertNotIn("apt-get", text)
        self.assertNotIn("pip install", text)
        self.assertIn("COPY addons /volumes/addons", text)


class _FakeGitHub:
    """Contents-API stub: an in-memory file dict."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.puts = []

    def get_content(self, full_name, path, ref=None):
        if path not in self.files:
            return None
        return {"text": self.files[path], "sha": "sha-%s" % path, "path": path}

    def put_content(self, full_name, path, text, message, branch=None, sha=None):
        self.puts.append(
            {"repo": full_name, "path": path, "sha": sha, "branch": branch, "message": message}
        )
        self.files[path] = text
        return {"content": {"path": path}}


class PipelineCase(TransactionCase):
    def setUp(self):
        super().setUp()
        no_push = self.env(context={"adomi_no_push": True, "adomi_config_no_push": True})
        self.client_rec = no_push["adomi.client"].create({"name": "Acme", "k8s_name": "acme"})
        self.environment = no_push["adomi.environment"].create(
            {"name": "Prod", "k8s_name": "production", "client_id": self.client_rec.id}
        )
        self.odoo_type = no_push["adomi.application.type"].create(
            {"name": "Odoo", "k8s_name": "odoo"}
        )
        self.repo = no_push["adomi.git.repository"].create(
            {
                "name": "acme/acme-odoo",
                "k8s_name": "acme-odoo",
                "url": "https://github.com/acme/acme-odoo",
                "default_branch": "master",
            }
        )
        self.app = no_push["adomi.application"].create(
            {
                "name": "ERP",
                "k8s_name": "erp",
                "client_id": self.client_rec.id,
                "environment_id": self.environment.id,
                "type_id": self.odoo_type.id,
                "git_repository_id": self.repo.id,
            }
        )
        self.github = _FakeGitHub()

    def _wire_github(self):
        app_rec = self.env["adomi.github.app"].create({"name": "Test App", "app_id": "1"})
        installation = self.env["adomi.github.installation"].create(
            {"app_id": app_rec.id, "installation_id": "42", "account_login": "acme"}
        )
        self.env["adomi.github.repository"].create(
            {"installation_id": installation.id, "full_name": "acme/acme-odoo"}
        )
        self.patch(type(installation), "_client", lambda inst: self.github)

    def test_commit_writes_manifest_and_dockerfile(self):
        self._wire_github()
        self.app.write(
            {
                "odoo_pip_packages": "stripe\n",
                "odoo_addon_source_ids": [
                    (
                        0,
                        0,
                        {
                            "repo_url": "https://github.com/OCA/web.git",
                            "branch": "19.0",
                            "modules": "web_responsive",
                        },
                    )
                ],
            }
        )

        self.app.action_odoo_commit_pipeline()

        paths = [p["path"] for p in self.github.puts]
        self.assertEqual(paths, [pipeline.MANIFEST_PATH, pipeline.DOCKERFILE_PATH])
        self.assertTrue(all(p["branch"] == "master" and p["sha"] is None for p in self.github.puts))
        self.assertIn("web_responsive", self.github.files[pipeline.DOCKERFILE_PATH])
        self.assertTrue(self.app.odoo_pipeline_synced_at)

        # Recommitting without changes is a no-op (no drift commits).
        self.github.puts = []
        self.app.action_odoo_commit_pipeline()
        self.assertEqual(self.github.puts, [])

        # A content change updates in place, passing the current blob sha.
        self.app.write({"odoo_pip_packages": "stripe\nrequests\n"})
        self.app.action_odoo_commit_pipeline()
        self.assertEqual(
            [(p["path"], p["sha"]) for p in self.github.puts],
            [
                (pipeline.MANIFEST_PATH, "sha-%s" % pipeline.MANIFEST_PATH),
                (pipeline.DOCKERFILE_PATH, "sha-%s" % pipeline.DOCKERFILE_PATH),
            ],
        )

    def test_import_reads_manifest_back(self):
        self._wire_github()
        self.github.files[pipeline.MANIFEST_PATH] = pipeline.render_manifest(SAMPLE)

        self.app.action_odoo_import_pipeline()

        self.assertEqual(self.app.odoo_base_image, "ghcr.io/adomi-io/odoo:19.0")
        self.assertEqual(self.app.odoo_pip_packages, "stripe\npython-slugify")
        self.assertEqual(len(self.app.odoo_addon_source_ids), 2)
        self.assertEqual(self.app.odoo_addon_source_ids[0].modules, "web_responsive")
        self.assertFalse(self.app.odoo_addon_source_ids[1].modules)

    def test_import_without_manifest_explains(self):
        self._wire_github()

        with self.assertRaises(UserError):
            self.app.action_odoo_import_pipeline()

    def test_commit_without_repo_or_connection_explains(self):
        self.app.write({"git_repository_id": False})
        with self.assertRaises(UserError):
            self.app.action_odoo_commit_pipeline()

        self.app.write({"git_repository_id": self.repo.id})
        # Repo linked but no GitHub installation mirrors it.
        with self.assertRaises(UserError):
            self.app.action_odoo_commit_pipeline()
