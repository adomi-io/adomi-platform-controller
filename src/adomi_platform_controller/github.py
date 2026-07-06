"""A tiny GitHub REST client: preview feedback on PRs + GitHub App auth.

Used by the Application engine to report a preview environment's URL back
to the originating PR (a comment) and to set a commit status on the PR head,
and to mint short-lived App installation tokens so builds can clone private
repositories. HTTP is the standard library ``urllib``; only the App JWT
signing needs a dependency (PyJWT + cryptography).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

import jwt

# Hidden marker so the preview comment can be found and updated in place.
PREVIEW_COMMENT_MARKER = "<!-- adomi-preview -->"

# Commit status context used for the preview check.
STATUS_CONTEXT = "adomi/preview"

# Hosts served by github.com App installations.
GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


def github_host(url: str) -> str:
    """The lowercased host of a git remote URL (https or ssh form)."""
    s = (url or "").strip()
    s = re.sub(r"^git@([^:]+):", r"https://\1/", s)
    s = re.sub(r"^[a-z+]+://", "", s)

    return s.split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0].lower()


def is_github_url(url: str) -> bool:
    """Whether a repository URL is hosted on github.com."""
    return github_host(url) in GITHUB_HOSTS


def preview_comment_body(url: str) -> str:
    """The PR comment body advertising the preview URL (carries the marker)."""
    return f"{PREVIEW_COMMENT_MARKER}\n🚀 **Preview environment** is ready: {url}"


def status_payload(state: str, target_url: str, description: str) -> dict:
    """The commit-status request body (pure, for tests)."""
    return {
        "state": state,  # pending | success | failure | error
        "target_url": target_url,
        "description": description[:140],
        "context": STATUS_CONTEXT,
    }


def _api_request(api: str, token: str, method: str, path: str, body: dict | None = None):
    """One authenticated GitHub REST call; returns the decoded JSON body."""
    data = json.dumps(body).encode() if body is not None else None

    req = urllib.request.Request(f"{api}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    if data is not None:
        req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed https API base
        raw = resp.read()

        return json.loads(raw) if raw else None


class GitHubClient:
    """Minimal authenticated GitHub REST client."""

    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        self._token = token
        self._api = api_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None):
        return _api_request(self._api, self._token, method, path, body)

    def set_commit_status(
        self, owner: str, repo: str, sha: str, state: str, target_url: str, description: str
    ) -> None:
        """Set a commit status on a SHA (pending/success/failure/error)."""
        self._request(
            "POST",
            f"/repos/{owner}/{repo}/statuses/{sha}",
            status_payload(state, target_url, description),
        )

    def upsert_pr_comment(self, owner: str, repo: str, number: int, body: str) -> None:
        """Create the preview comment, or update the existing marked one in place."""
        existing = self._request("GET", f"/repos/{owner}/{repo}/issues/{number}/comments") or []

        for c in existing:
            if PREVIEW_COMMENT_MARKER in (c.get("body") or ""):
                self._request(
                    "PATCH",
                    f"/repos/{owner}/{repo}/issues/comments/{c['id']}",
                    {"body": body},
                )

                return

        self._request("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body})


class GitHubAppError(RuntimeError):
    """The GitHub App cannot vouch for a repository (not installed on it)."""


class GitHubAppAuth:
    """GitHub App credentials: mints short-lived installation access tokens.

    The standard CI pattern (actions-runner-controller, Flux): nothing durable
    is ever stored — each build gets a fresh ~1h token scoped to just the
    repository it clones, minted from the App's private key on demand.
    """

    # Installation tokens live 60 minutes; hand out a cached one only while it
    # comfortably outlives any build that starts with it.
    TOKEN_TTL_SECONDS = 45 * 60

    def __init__(
        self, app_id: str, private_key: str, api_url: str = "https://api.github.com"
    ) -> None:
        self._app_id = str(app_id).strip()
        self._private_key = private_key
        self._api = api_url.rstrip("/")
        # (owner, repo) -> (token, monotonic deadline)
        self._tokens: dict[tuple[str, str], tuple[str, float]] = {}

    def fingerprint(self) -> tuple[str, str]:
        """Identity for cache reuse: same App + endpoint means same tokens."""
        return (self._api, self._app_id)

    def _app_jwt(self) -> str:
        now = int(time.time())

        return jwt.encode(
            # iat backdated for clock skew; GitHub caps exp at now+10min.
            {"iat": now - 60, "exp": now + 9 * 60, "iss": self._app_id},
            self._private_key,
            algorithm="RS256",
        )

    def installation_token_for(self, owner: str, repo: str) -> str:
        """A contents:read token scoped to one repository (cached ~45 min)."""
        key = (owner, repo)
        cached = self._tokens.get(key)

        if cached and cached[1] > time.monotonic():
            return cached[0]

        app_jwt = self._app_jwt()

        try:
            installation = _api_request(
                self._api, app_jwt, "GET", f"/repos/{owner}/{repo}/installation"
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise GitHubAppError(f"the GitHub App is not installed on {owner}/{repo}") from exc

            raise

        minted = _api_request(
            self._api,
            app_jwt,
            "POST",
            f"/app/installations/{installation['id']}/access_tokens",
            {"repositories": [repo], "permissions": {"contents": "read"}},
        )

        token = minted["token"]
        self._tokens[key] = (token, time.monotonic() + self.TOKEN_TTL_SECONDS)

        return token


# Reused across reconcile passes so the per-repo token cache stays warm (the
# build handler re-runs every poll while a Workflow is building).
_app_auth_cache: dict[tuple[str, str], GitHubAppAuth] = {}


def app_auth(
    app_id: str, private_key: str, api_url: str = "https://api.github.com"
) -> GitHubAppAuth:
    """A (cached) GitHubAppAuth for these App credentials."""
    candidate = GitHubAppAuth(app_id, private_key, api_url)
    cached = _app_auth_cache.get(candidate.fingerprint())

    # Rotated private key: replace the cached instance and its tokens.
    if cached is not None and cached._private_key == private_key:
        return cached

    _app_auth_cache[candidate.fingerprint()] = candidate

    return candidate
