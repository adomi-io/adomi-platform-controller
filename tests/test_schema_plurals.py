"""Tripwire: the set of resource plurals is part of the platform's wire contract.

The kubernetes-provisioner's client chart (charts/client/values.yaml,
includeGlobs) whitelists exactly these directories of a client repo for Argo CD
to sync. A plural added here without updating that glob means the API commits
manifests that Argo CD silently never applies — the record sits at "waiting for
the platform to apply it" forever (this is exactly how DatabaseServers shipped
broken). If this test fails, update the provisioner's includeGlobs (and this
list) in the same change.
"""

from adomi_platform_schema import BY_PLURAL

CONTRACT_PLURALS = {
    "applications",
    "clients",
    "databases",
    "databaseservers",
    "domains",
    "environments",
    "gitrepositories",
    "snapshots",
}


def test_plurals_match_provisioner_include_globs_contract():
    assert set(BY_PLURAL.keys()) == CONTRACT_PLURALS, (
        "Resource plurals changed. Update kubernetes-provisioner "
        "charts/client/values.yaml includeGlobs to include the new plural's "
        "directory, then update CONTRACT_PLURALS here."
    )
