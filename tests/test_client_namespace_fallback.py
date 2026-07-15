"""Customer-scoped DatabaseServer resolution from environment-namespace Databases.

Regression guard: the odoo chart emits a Database CR into the environment
namespace (acme-production) while a customer-scoped DatabaseServer's CR lives in
the client's CR namespace (adomi-client-acme). Resolving the serverRef only in
the Database's own namespace left the Database at DependencyNotMet forever.
find_client_namespace locates the client's CR namespace from the Client CR
itself, without assuming the provisioner's namespace prefix.
"""

from __future__ import annotations

from adomi_platform_controller import resolve


class _FakeApi:
    def __init__(self, items):
        self._items = items

    def list_cluster_custom_object(self, group, version, plural):
        assert plural == resolve.PLURAL_CLIENTS
        return {"items": self._items}


def _client_item(name, namespace, slug=None):
    item = {"metadata": {"name": name, "namespace": namespace}, "spec": {}}

    if slug:
        item["spec"]["slug"] = slug

    return item


def test_find_client_namespace_by_name(monkeypatch):
    api = _FakeApi([_client_item("acme", "adomi-client-acme")])
    monkeypatch.setattr(resolve.client, "CustomObjectsApi", lambda: api)

    assert resolve.find_client_namespace("acme") == "adomi-client-acme"


def test_find_client_namespace_by_slug(monkeypatch):
    api = _FakeApi([_client_item("acme-corp", "adomi-client-acme", slug="acme")])
    monkeypatch.setattr(resolve.client, "CustomObjectsApi", lambda: api)

    assert resolve.find_client_namespace("acme") == "adomi-client-acme"


def test_find_client_namespace_missing(monkeypatch):
    api = _FakeApi([_client_item("other", "adomi-client-other")])
    monkeypatch.setattr(resolve.client, "CustomObjectsApi", lambda: api)

    assert resolve.find_client_namespace("acme") is None
