"""End-to-end smoke test of the portal write path against a LIVE stack.

Drives a running Odoo portal over JSON-RPC exactly the way the UI does (the ORM
write fires the same k8s_mixin push), and asserts the intent lands as a git
commit in the client's Forgejo repo:

    Odoo write() -> k8s_mixin -> PlatformApiClient -> platform API -> Forgejo

The test renames an EXISTING client's display name to a nonced value, waits for
the commit to appear in ``clients/<slug>.yaml``, then restores the original name
(which must also commit). No resources are created or deleted, so it is safe to
run against a real environment.

Skipped unless the ``ADOMI_E2E_*`` environment is present:

    ADOMI_E2E_ODOO_URL        e.g. http://localhost:8069
    ADOMI_E2E_ODOO_DB         Odoo database name
    ADOMI_E2E_ODOO_LOGIN      Odoo user (its writes must route to the API backend)
    ADOMI_E2E_ODOO_PASSWORD   that user's password or API key
    ADOMI_E2E_FORGEJO_URL     e.g. http://localhost:3000 (reachable from the test)
    ADOMI_E2E_FORGEJO_TOKEN   token able to read the client org's repos
    ADOMI_E2E_CLIENT          existing client slug to exercise (default: acme)
    ADOMI_E2E_FORGEJO_ORG     Forgejo org holding client repos (default: clients)
"""

import base64
import json
import os
import time
import urllib.request
import uuid

import pytest

_REQUIRED = (
    "ADOMI_E2E_ODOO_URL",
    "ADOMI_E2E_ODOO_DB",
    "ADOMI_E2E_ODOO_LOGIN",
    "ADOMI_E2E_ODOO_PASSWORD",
    "ADOMI_E2E_FORGEJO_URL",
    "ADOMI_E2E_FORGEJO_TOKEN",
)

pytestmark = pytest.mark.skipif(
    any(not os.environ.get(v) for v in _REQUIRED),
    reason="live-stack e2e: set the ADOMI_E2E_* environment to run",
)

COMMIT_TIMEOUT = 90  # seconds to wait for a portal write to land in git


def _cfg(name, default=None):
    return os.environ.get(name, default)


class Odoo:
    """Minimal JSON-RPC client for the portal (same entrypoint the web UI uses)."""

    def __init__(self, url, db, login, password):
        self.url = url.rstrip("/") + "/jsonrpc"
        self.db = db
        self.password = password
        self.uid = self._call("common", "login", [db, login, password])
        assert self.uid, "Odoo login failed for %r" % login

    def _call(self, service, method, args):
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": 1,
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        body = json.load(urllib.request.urlopen(req, timeout=30))
        if body.get("error"):
            raise AssertionError("Odoo RPC error: %s" % body["error"])
        return body.get("result")

    def execute(self, model, method, *args):
        return self._call(
            "object",
            "execute_kw",
            [self.db, self.uid, self.password, model, method, list(args)],
        )


class Forgejo:
    """Reads a file from the client repo via the contents API."""

    def __init__(self, url, token, org):
        self.base = url.rstrip("/")
        self.token = token
        self.org = org

    def read_client_file(self, slug):
        req = urllib.request.Request(
            "%s/api/v1/repos/%s/%s/contents/clients/%s.yaml" % (self.base, self.org, slug, slug),
            headers={"Authorization": "token %s" % self.token},
        )
        body = json.load(urllib.request.urlopen(req, timeout=30))
        return body["sha"], base64.b64decode(body["content"]).decode()


def _wait_for(predicate, timeout=COMMIT_TIMEOUT, interval=3):
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result:
            return result
        if time.monotonic() > deadline:
            return None
        time.sleep(interval)


@pytest.fixture(scope="module")
def odoo():
    return Odoo(
        _cfg("ADOMI_E2E_ODOO_URL"),
        _cfg("ADOMI_E2E_ODOO_DB"),
        _cfg("ADOMI_E2E_ODOO_LOGIN"),
        _cfg("ADOMI_E2E_ODOO_PASSWORD"),
    )


@pytest.fixture(scope="module")
def forgejo():
    return Forgejo(
        _cfg("ADOMI_E2E_FORGEJO_URL"),
        _cfg("ADOMI_E2E_FORGEJO_TOKEN"),
        _cfg("ADOMI_E2E_FORGEJO_ORG", "clients"),
    )


def test_portal_write_reaches_git(odoo, forgejo):
    slug = _cfg("ADOMI_E2E_CLIENT", "acme")
    ids = odoo.execute("adomi.client", "search", [["k8s_name", "=", slug]])
    assert ids, "client %r not found in the portal" % slug
    (record,) = odoo.execute("adomi.client", "read", ids[:1], ["name"])
    original = record["name"]

    nonce = "e2e-%s" % uuid.uuid4().hex[:8]
    sha_before, _content = forgejo.read_client_file(slug)

    try:
        odoo.execute("adomi.client", "write", ids[:1], {"name": "%s [%s]" % (original, nonce)})
        landed = _wait_for(
            lambda: (lambda sha, content: nonce in content and sha != sha_before)(
                *forgejo.read_client_file(slug)
            )
        )
        assert landed, "portal write did not reach %s/%s within %ss" % (
            forgejo.org,
            slug,
            COMMIT_TIMEOUT,
        )
    finally:
        odoo.execute("adomi.client", "write", ids[:1], {"name": original})

    restored = _wait_for(lambda: nonce not in forgejo.read_client_file(slug)[1])
    assert restored, "restore write did not reach git within %ss" % COMMIT_TIMEOUT

    # The record must not be left in a failed-sync state.
    (record,) = odoo.execute("adomi.client", "read", ids[:1], ["name", "k8s_state"])
    assert record["name"] == original
    assert record.get("k8s_state") != "unknown", record
