"""A tiny GitHub REST client for posting preview feedback to pull requests.

Used by the Application engine to report a preview environment's URL back
to the originating PR (a comment) and to set a commit status on the PR head. Built
on the standard library ``urllib`` to avoid adding a dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

# Hidden marker so the preview comment can be found and updated in place.
PREVIEW_COMMENT_MARKER = "<!-- adomi-preview -->"

# Commit status context used for the preview check.
STATUS_CONTEXT = "adomi/preview"


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


class GitHubClient:
    """Minimal authenticated GitHub REST client."""

    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        self._token = token
        self._api = api_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self._api}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed https API base
            raw = resp.read()
            return json.loads(raw) if raw else None

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
                    "PATCH", f"/repos/{owner}/{repo}/issues/comments/{c['id']}", {"body": body}
                )
                return
        self._request("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body})
