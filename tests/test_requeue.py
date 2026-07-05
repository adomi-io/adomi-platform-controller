"""Cross-scope fan-out: parents stamp dependent Applications to re-render them."""

from __future__ import annotations

import pytest

pytest.importorskip("kubernetes")

from adomi_platform_controller import requeue  # noqa: E402


class _FakeApi:
    def __init__(self, items, fail_list=False, fail_patch_for=()):
        self.items = items
        self.fail_list = fail_list
        self.fail_patch_for = set(fail_patch_for)
        self.patches = []
        self.list_calls = []

    def list_namespaced_custom_object(self, group, version, namespace, plural):
        self.list_calls.append(("namespaced", namespace))
        if self.fail_list:
            raise RuntimeError("boom")
        return {"items": [i for i in self.items if i["metadata"]["namespace"] == namespace]}

    def list_cluster_custom_object(self, group, version, plural):
        self.list_calls.append(("cluster", None))
        if self.fail_list:
            raise RuntimeError("boom")
        return {"items": self.items}

    def patch_namespaced_custom_object(self, group, version, namespace, plural, name, body):
        if name in self.fail_patch_for:
            raise RuntimeError("patch denied")
        self.patches.append((namespace, name, body))


def _app(name, namespace="acme", annotations=None, spec=None):
    return {
        "metadata": {"name": name, "namespace": namespace, "annotations": annotations or {}},
        "spec": spec or {},
    }


@pytest.fixture
def api(monkeypatch):
    holder = {}

    def install(fake):
        holder["api"] = fake
        monkeypatch.setattr(requeue.client, "CustomObjectsApi", lambda: fake)
        return fake

    return install


def test_revision_format():
    assert requeue.revision("client", "acme", 3) == "client/acme@3"
    assert requeue.revision("domain", "apex", None) == "domain/apex@0"


def test_stamps_only_stale_matching_apps(api):
    rev = "environment/production@2"
    fake = api(
        _FakeApi(
            [
                _app("erp", spec={"environmentRef": {"name": "production"}}),
                _app("bi", spec={"environmentRef": {"name": "staging"}}),
                _app(
                    "done",
                    annotations={requeue.ANNOTATION: rev},
                    spec={"environmentRef": {"name": "production"}},
                ),
            ]
        )
    )

    count = requeue.requeue_applications(
        rev,
        namespace="acme",
        predicate=requeue.app_references_environment("production"),
    )

    assert count == 1
    assert fake.patches == [
        ("acme", "erp", {"metadata": {"annotations": {requeue.ANNOTATION: rev}}})
    ]


def test_cluster_wide_without_namespace(api):
    fake = api(_FakeApi([_app("erp"), _app("other", namespace="globex")]))

    count = requeue.requeue_applications("organization/adomi@5")

    assert count == 2
    assert fake.list_calls == [("cluster", None)]


def test_type_and_domain_predicates():
    erp = _app("erp", spec={"type": "odoo", "domainRef": {"name": "apex"}})

    assert requeue.app_references_type("odoo")(erp)
    assert not requeue.app_references_type("superset")(erp)
    assert requeue.app_references_domain("apex")(erp)
    assert not requeue.app_references_domain("other")(erp)


def test_failures_are_swallowed(api):
    # Listing failure: fan-out is an accelerator, never a reason to fail the parent.
    api(_FakeApi([], fail_list=True))
    assert requeue.requeue_applications("client/acme@1", namespace="acme") == 0

    # One app's patch failing doesn't stop the rest.
    fake = api(_FakeApi([_app("bad"), _app("good")], fail_patch_for={"bad"}))
    assert requeue.requeue_applications("client/acme@2", namespace="acme") == 1
    assert [p[1] for p in fake.patches] == ["good"]
