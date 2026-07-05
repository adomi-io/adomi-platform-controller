"""Forgejo (Gitea-compatible) implementation of the git backend.

No FastAPI import and an injectable ``session`` (anything exposing
``request(method, url, headers=, content=, timeout=)``), so the git logic is
unit-testable without a network. In the running service the default session is an
``httpx.Client``.

Contents API used here:
  GET    /api/v1/repos/{owner}/{repo}/contents/{path}?ref={branch}   -> file (sha)
  POST   /api/v1/repos/{owner}/{repo}/contents/{path}                 -> create (no sha)
  PUT    /api/v1/repos/{owner}/{repo}/contents/{path}                 -> update (sha required)
  DELETE /api/v1/repos/{owner}/{repo}/contents/{path}                 -> delete
  POST   /api/v1/orgs/{owner}/repos                                   -> create repo
  POST   /api/v1/repos/{owner}/{repo}/pulls                           -> open PR
  GET    /api/v1/orgs/{owner}                                         -> readiness probe
"""

from __future__ import annotations

import base64
import json

from .base import MODE_COMMIT, MODE_PR, GitError, Readiness


def _default_session(verify: bool, timeout: float):
    import httpx

    return httpx.Client(verify=verify, timeout=timeout)


class ForgejoWriter:
    """Commits CR manifests to a customer's repo under an org (one repo per client)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        owner: str,
        *,
        session=None,
        default_branch: str = "main",
        timeout: float = 15.0,
        verify: bool = True,
    ):
        if not base_url:
            raise GitError("Forgejo base URL is not configured.")
        if not token:
            raise GitError("Forgejo token is not configured.")

        self.base_url = base_url.rstrip("/")
        self.token = token
        self.owner = owner or "clients"
        self.default_branch = default_branch or "main"
        self.timeout = timeout
        self.verify = verify
        self._session = session

    @property
    def session(self):
        if self._session is None:
            self._session = _default_session(self.verify, self.timeout)

        return self._session

    # --- low-level HTTP ---------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1/{path.lstrip('/')}"

    def _headers(self) -> dict:
        return {
            "Authorization": f"token {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, payload=None, params=None):
        url = self._url(path)

        if params:
            url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

        content = json.dumps(payload).encode("utf-8") if payload is not None else None

        try:
            return self.session.request(
                method,
                url,
                headers=self._headers(),
                content=content,
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - surface network errors uniformly
            raise GitError(f"Forgejo request failed: {exc}") from exc

    @staticmethod
    def _json(resp) -> dict:
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {}

    # --- repo + file primitives -------------------------------------------------
    def ensure_repo(self, repo: str, *, description: str = "") -> bool:
        resp = self._request("GET", f"repos/{self.owner}/{repo}")

        if resp.status_code == 200:
            return False
        if resp.status_code != 404:
            raise GitError(
                f"Checking repo {self.owner}/{repo} failed: {resp.status_code} {resp.text}"
            )

        payload = {
            "name": repo,
            "auto_init": True,
            "private": True,
            "default_branch": self.default_branch,
        }
        if description:
            payload["description"] = description

        resp = self._request("POST", f"orgs/{self.owner}/repos", payload)

        if resp.status_code in (200, 201):
            return True
        if resp.status_code == 409:  # created concurrently
            return False

        raise GitError(f"Creating repo {self.owner}/{repo} failed: {resp.status_code} {resp.text}")

    def _file_sha(self, repo: str, path: str, ref: str) -> str | None:
        resp = self._request(
            "GET", f"repos/{self.owner}/{repo}/contents/{path}", params={"ref": ref}
        )

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitError(f"Reading {path} failed: {resp.status_code} {resp.text}")

        return self._json(resp).get("sha")

    def _put_file(self, repo, path, content, message, branch, *, sha=None, new_branch=None):
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        if new_branch:
            payload["new_branch"] = new_branch

        # The contents API distinguishes create (POST, no sha) from update (PUT,
        # sha required) — a PUT without a sha is rejected with 422 "[SHA]: Required".
        method = "PUT" if sha else "POST"
        resp = self._request(method, f"repos/{self.owner}/{repo}/contents/{path}", payload)

        if resp.status_code not in (200, 201):
            raise GitError(f"Writing {path} failed: {resp.status_code} {resp.text}")

        return self._json(resp)

    def _delete_file(self, repo, path, message, branch, sha, *, new_branch=None):
        payload = {"message": message, "sha": sha, "branch": branch}
        if new_branch:
            payload["new_branch"] = new_branch

        resp = self._request("DELETE", f"repos/{self.owner}/{repo}/contents/{path}", payload)

        if resp.status_code not in (200, 201):
            raise GitError(f"Deleting {path} failed: {resp.status_code} {resp.text}")

        return self._json(resp)

    def _open_pr(self, repo, head, base, title, body=""):
        payload = {"head": head, "base": base, "title": title, "body": body}
        resp = self._request("POST", f"repos/{self.owner}/{repo}/pulls", payload)

        if resp.status_code in (200, 201):
            return self._json(resp)
        if resp.status_code in (409, 422):  # a PR for head->base already exists
            return {}

        raise GitError(f"Opening PR for {repo} failed: {resp.status_code} {resp.text}")

    # --- GitWriter contract -----------------------------------------------------
    def apply_manifest(self, repo, path, content, message, *, mode=MODE_COMMIT) -> dict:
        self.ensure_repo(repo)
        base_branch = self.default_branch

        if mode == MODE_PR:
            work_branch = f"adomi/{path.replace('/', '-').rsplit('.', 1)[0]}"
            sha = self._file_sha(repo, path, base_branch)
            self._put_file(
                repo, path, content, message, base_branch, sha=sha, new_branch=work_branch
            )
            pr = self._open_pr(repo, work_branch, base_branch, message)

            return {"committed": True, "branch": work_branch, "pr": pr}

        sha = self._file_sha(repo, path, base_branch)
        self._put_file(repo, path, content, message, base_branch, sha=sha)

        return {"committed": True, "branch": base_branch}

    def read_manifest(self, repo, path) -> str | None:
        resp = self._request(
            "GET", f"repos/{self.owner}/{repo}/contents/{path}", params={"ref": self.default_branch}
        )

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitError(f"Reading {path} failed: {resp.status_code} {resp.text}")

        payload = self._json(resp)
        try:
            return base64.b64decode(payload.get("content") or "").decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise GitError(f"Decoding {path} failed: {exc}") from exc

    def list_tree(self, repo, ref=None) -> list[dict]:
        """Every file in the repo at ``ref`` (default branch when omitted).

        Empty/missing repos list as [] — the portal shows an empty panel, not
        an error, for a customer whose first commit hasn't landed yet.
        """
        ref = ref or self.default_branch
        resp = self._request(
            "GET",
            f"repos/{self.owner}/{repo}/git/trees/{ref}",
            params={"recursive": "true"},
        )

        if resp.status_code in (404, 409):
            return []
        if resp.status_code != 200:
            raise GitError(f"Listing tree of {repo} failed: {resp.status_code} {resp.text}")

        entries = self._json(resp).get("tree") or []

        return [
            {
                "path": e.get("path") or "",
                "type": "dir" if e.get("type") == "tree" else "file",
                "size": e.get("size") or 0,
            }
            for e in entries
            if e.get("path")
        ]

    def list_commits(self, repo, limit=10, ref=None) -> list[dict]:
        """The most recent commits on ``ref`` (default branch when omitted)."""
        params = {
            "limit": int(limit),
            "stat": "false",
            "verification": "false",
            "files": "false",
        }
        if ref:
            params["sha"] = ref

        resp = self._request("GET", f"repos/{self.owner}/{repo}/commits", params=params)

        if resp.status_code in (404, 409):  # missing or empty repo
            return []
        if resp.status_code != 200:
            raise GitError(f"Listing commits of {repo} failed: {resp.status_code} {resp.text}")

        out = []
        for c in self._json(resp) or []:
            commit = c.get("commit") or {}
            author = commit.get("author") or {}
            out.append(
                {
                    "sha": (c.get("sha") or "")[:10],
                    "message": (commit.get("message") or "").splitlines()[0]
                    if commit.get("message")
                    else "",
                    "author": author.get("name") or "",
                    "date": author.get("date") or "",
                    "url": c.get("html_url") or "",
                }
            )

        return out

    def delete_manifest(self, repo, path, message, *, mode=MODE_COMMIT) -> dict:
        base_branch = self.default_branch
        sha = self._file_sha(repo, path, base_branch)

        if not sha:
            return {"deleted": False, "reason": "absent"}

        if mode == MODE_PR:
            work_branch = f"adomi/delete-{path.replace('/', '-').rsplit('.', 1)[0]}"
            self._delete_file(repo, path, message, base_branch, sha, new_branch=work_branch)
            pr = self._open_pr(repo, work_branch, base_branch, message)

            return {"deleted": True, "branch": work_branch, "pr": pr}

        self._delete_file(repo, path, message, base_branch, sha)

        return {"deleted": True, "branch": base_branch}

    def check_ready(self) -> Readiness:
        try:
            resp = self._request("GET", f"orgs/{self.owner}")
        except GitError as exc:
            return Readiness.down(str(exc))

        if resp.status_code == 200:
            return Readiness.up()

        return Readiness.down(f"forgejo org {self.owner!r}: {resp.status_code}")
