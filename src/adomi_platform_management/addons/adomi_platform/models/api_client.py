"""Thin client for the Adomi platform API.

When the addon's write backend is ``api`` (see ``adomi_platform.write_backend``),
creating/editing a customer-owned record sends its intent (the CR ``spec``) to the
platform API, which builds the full custom resource and commits it to that customer's
tenant git repo. The API owns the Forgejo credentials and the repo / namespace / kind
conventions, so this client is deliberately tiny.

Free of any Odoo import so it can be unit-tested with a stub HTTP session. The mixin
builds a client from config and wraps these calls.
"""

import json

UPSERT_PATH = "/v1/tenants/%s/%s/%s"  # client, plural, name


class PlatformApiError(Exception):
    """A platform-API call failed (network or non-2xx response)."""


class PlatformApiClient:
    """Calls the platform API's tenant resource endpoints.

    ``session`` is any object exposing ``request(method, url, headers=, data=,
    timeout=)`` returning a response with ``status_code`` and ``text`` (the
    ``requests`` library satisfies this; tests pass a stub).
    """

    def __init__(self, base_url, token, session, *, timeout=15, verify=True):
        if not base_url:
            raise PlatformApiError("Platform API URL is not configured.")
        self.base_url = base_url.rstrip("/")
        self.token = token or ""
        self.session = session
        self.timeout = timeout
        self.verify = verify

    def _headers(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        return headers

    def _request(self, method, path, payload=None):
        url = self.base_url + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None

        try:
            resp = self.session.request(
                method,
                url,
                headers=self._headers(),
                data=data,
                timeout=self.timeout,
                verify=self.verify,
            )
        except Exception as exc:  # noqa: BLE001 - surface network errors uniformly
            raise PlatformApiError("Platform API request failed: %s" % exc) from exc

        if resp.status_code not in (200, 201, 204):
            raise PlatformApiError(
                "Platform API %s %s -> %s %s"
                % (method, path, resp.status_code, getattr(resp, "text", ""))
            )

        return resp

    def upsert(self, client, plural, name, spec, labels=None):
        """Create/update a resource by committing its CR to the tenant repo."""
        body = {"spec": spec or {}}
        if labels:
            body["labels"] = labels
        return self._request("PUT", UPSERT_PATH % (client, plural, name), body)

    def delete(self, client, plural, name):
        """Remove a resource's CR from the tenant repo."""
        return self._request("DELETE", UPSERT_PATH % (client, plural, name))
