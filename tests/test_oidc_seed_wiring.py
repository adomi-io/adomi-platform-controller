"""The Authentik OIDC provider must re-seed from env on every module update.

Regression guard for the first-install race: the portal pod once booted before
the management-sso Secret existed and never seeded the provider, because
setup_from_env only ran on fresh install (post_init_hook) or one-off migrations.
The fix is a non-noupdate <function> data record, re-executed by the ``-u
adomi_platform`` every pod boot. These tests pin that wiring in place.
"""

import ast
import os
import xml.etree.ElementTree as ET

ADDON = os.path.join(
    os.path.dirname(__file__),
    "..",
    "src",
    "adomi_platform_management",
    "addons",
    "adomi_platform",
)


def load_manifest():
    with open(os.path.join(ADDON, "__manifest__.py")) as f:
        return ast.literal_eval(f.read())


def test_manifest_loads_oidc_setup_data():
    assert "data/oidc_setup.xml" in load_manifest()["data"]


def test_oidc_setup_function_runs_on_every_update():
    tree = ET.parse(os.path.join(ADDON, "data", "oidc_setup.xml"))

    calls = [
        node
        for node in tree.getroot().iter("function")
        if node.get("model") == "adomi.oidc.setup" and node.get("name") == "setup_from_env"
    ]
    assert calls, "data/oidc_setup.xml must call adomi.oidc.setup.setup_from_env"

    # noupdate would restrict the call to install only, resurrecting the race.
    for data in tree.getroot().iter("data"):
        assert data.get("noupdate") in (None, "0", "False")
