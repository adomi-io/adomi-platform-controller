"""Forgejo push webhook: signature validation + the client-side push handler."""

import hashlib
import hmac

from odoo.tests.common import TransactionCase

from odoo.addons.adomi_platform.controllers.main import valid_forgejo_signature


class TestForgejoSignature(TransactionCase):
    def test_valid_signature_matches(self):
        raw = b'{"repository": {"name": "acme"}}'
        sig = hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()

        self.assertTrue(valid_forgejo_signature(raw, "s3cret", sig))
        self.assertTrue(valid_forgejo_signature(raw, "s3cret", sig.upper()))

    def test_bad_signature_or_missing_secret_rejected(self):
        raw = b"{}"
        sig = hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()

        self.assertFalse(valid_forgejo_signature(raw, "s3cret", "deadbeef"))
        self.assertFalse(valid_forgejo_signature(raw, "", sig))
        self.assertFalse(valid_forgejo_signature(raw, "s3cret", ""))
        self.assertFalse(valid_forgejo_signature(b"tampered", "s3cret", sig))


class TestOnRepoPush(TransactionCase):
    def setUp(self):
        super().setUp()
        no_push = self.env(context={"adomi_no_push": True})
        self.org = no_push["adomi.organization"].create(
            {"name": "Acme Org", "k8s_name": "acme-org"}
        )
        self.client = no_push["adomi.client"].create(
            {"name": "Acme", "k8s_name": "acme", "organization_id": self.org.id}
        )
        self.cron = self.env.ref("adomi_platform.cron_sync_platform")

    def _trigger_count(self):
        return self.env["ir.cron.trigger"].search_count([("cron_id", "=", self.cron.id)])

    def test_push_for_known_repo_triggers_reconcile(self):
        before = self._trigger_count()

        self.assertTrue(self.env["adomi.client"]._on_repo_push("acme"))
        # One immediate run + one delayed run for the state this push is still
        # rolling out through Argo CD and the controller.
        self.assertEqual(self._trigger_count(), before + 2)

    def test_push_for_unknown_repo_is_ignored(self):
        before = self._trigger_count()

        self.assertFalse(self.env["adomi.client"]._on_repo_push("not-a-client"))
        self.assertFalse(self.env["adomi.client"]._on_repo_push(""))
        self.assertEqual(self._trigger_count(), before)
