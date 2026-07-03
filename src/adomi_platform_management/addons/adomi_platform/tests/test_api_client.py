"""Unit tests for the platform API client (no Odoo runtime needed)."""

import json
import unittest

from odoo.addons.adomi_platform.models import api_client


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _StubSession:
    def __init__(self, status=200):
        self.calls = []
        self.status = status

    def request(self, method, url, headers=None, data=None, timeout=None, verify=True):
        body = json.loads(data.decode("utf-8")) if data else None
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        return _Resp(self.status)


def _client(session, token="t"):
    return api_client.PlatformApiClient("https://api.example.com", token, session)


class TestPlatformApiClient(unittest.TestCase):
    def test_upsert_puts_body(self):
        s = _StubSession()
        _client(s).upsert(
            "/v1/clients/acme/environments/prod/applications/erp",
            {"type": "odoo", "host": "erp.acme.example.com"},
        )
        call = s.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertTrue(
            call["url"].endswith("/v1/clients/acme/environments/prod/applications/erp")
        )
        self.assertEqual(call["body"], {"type": "odoo", "host": "erp.acme.example.com"})
        self.assertEqual(call["headers"]["Authorization"], "Bearer t")

    def test_delete(self):
        s = _StubSession(status=204)
        _client(s).delete("/v1/clients/acme")
        self.assertEqual(s.calls[0]["method"], "DELETE")
        self.assertTrue(s.calls[0]["url"].endswith("/v1/clients/acme"))

    def test_non_2xx_raises(self):
        with self.assertRaises(api_client.PlatformApiError):
            _client(_StubSession(status=400)).upsert("/v1/clients/acme", {})

    def test_missing_url_raises(self):
        with self.assertRaises(api_client.PlatformApiError):
            api_client.PlatformApiClient("", "t", _StubSession())

    def test_no_token_omits_header(self):
        s = _StubSession()
        api_client.PlatformApiClient("https://x", "", s).upsert("/v1/clients/a", {})
        self.assertNotIn("Authorization", s.calls[0]["headers"])


if __name__ == "__main__":
    unittest.main()
