"""End-to-end check of GitHub App build auth against the LIVE GitHub API.

Exercises exactly what the build path does for a private github.com repository:
sign an App JWT from the configured credentials, resolve the repository's
installation, mint a contents:read installation token scoped to that one repo,
and prove the token can actually read the repository over the git/REST surface.

Nothing durable is created; installation tokens expire on their own within an
hour.

Skipped unless the ``ADOMI_E2E_GITHUB_*`` environment is present:

    ADOMI_E2E_GITHUB_APP_ID     the GitHub App's numeric ID
    ADOMI_E2E_GITHUB_APP_KEY    path to the App's private key PEM file
    ADOMI_E2E_GITHUB_REPO       a repo the App is installed on, as "owner/name"
"""

import os
import urllib.request

import pytest

from adomi_platform_controller import github

APP_ID = os.environ.get("ADOMI_E2E_GITHUB_APP_ID")
APP_KEY = os.environ.get("ADOMI_E2E_GITHUB_APP_KEY")
REPO = os.environ.get("ADOMI_E2E_GITHUB_REPO")

pytestmark = pytest.mark.skipif(
    not (APP_ID and APP_KEY and REPO),
    reason="ADOMI_E2E_GITHUB_* environment not configured",
)


def test_mint_and_use_installation_token():
    owner, repo = REPO.split("/", 1)

    with open(APP_KEY) as fh:
        private_key = fh.read()

    token = github.GitHubAppAuth(APP_ID, private_key).installation_token_for(owner, repo)

    assert token

    # The token must be able to read the repository — the same access BuildKit
    # needs (GIT_AUTH_TOKEN sends it as basic auth, username x-access-token).
    req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")

    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
