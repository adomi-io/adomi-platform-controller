"""A thin, loosely-coupled GitHub REST client.

The portal is an API *consumer*: it never embeds GitHub logic, it just makes HTTP
calls (identity, repos, branches, PRs, issues) so Odoo can orchestrate the
odoo.sh-style workflow and mirror state.

Auth is a GitHub App: an App-JWT (signed with the App private key) authenticates
*as the App* (``GitHubAppClient``); it mints short-lived per-installation tokens
that authorize the day-to-day REST calls (``GitHubClient``). Both speak the same
REST surface, so callers don't care which token they hold.
"""

import hashlib
import hmac
import time

API_ROOT = "https://api.github.com"


class GitHubError(Exception):
    """Raised on a non-2xx GitHub response (carries status + message)."""

    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__("GitHub %s: %s" % (status, message))


class GitHubClient:
    def __init__(self, token, requests, api_root=API_ROOT):
        self._token = token
        self._requests = requests
        self._api_root = (api_root or API_ROOT).rstrip("/")

    # --- low level ---
    def _request(self, method, path, payload=None, params=None):
        url = path if path.startswith("http") else self._api_root + path
        resp = self._requests.request(
            method,
            url,
            json=payload,
            params=params,
            headers={
                "Authorization": "Bearer %s" % self._token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "adomi-platform-management",
            },
            timeout=30,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            message = ""
            try:
                message = (resp.json() or {}).get("message", "")
            except Exception:  # noqa: BLE001 - non-JSON error body
                message = resp.text[:200]
            raise GitHubError(resp.status_code, message or resp.reason)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # --- identity ---
    def user(self):
        """The authenticated account (login, name, avatar_url, …)."""
        return self._request("GET", "/user")

    def orgs(self):
        """Organizations the token can act on."""
        return self._request("GET", "/user/orgs", params={"per_page": 100})

    # --- repositories ---
    def list_repos(self, owner=None):
        """Repos for the user (owner=None) or a specific org."""
        if owner:
            return self._request(
                "GET", "/orgs/%s/repos" % owner, params={"per_page": 100, "sort": "updated"}
            )
        return self._request(
            "GET", "/user/repos", params={"per_page": 100, "sort": "updated", "affiliation": "owner"}
        )

    def get_repo(self, full_name):
        return self._request("GET", "/repos/%s" % full_name)

    def installation_repos(self):
        """Repos visible to the installation whose token this client holds."""
        return self._request("GET", "/installation/repositories", params={"per_page": 100})

    def create_repo(self, name, owner=None, private=True, description="", auto_init=True):
        payload = {
            "name": name,
            "private": private,
            "description": description,
            "auto_init": auto_init,
        }
        if owner:
            return self._request("POST", "/orgs/%s/repos" % owner, payload)
        return self._request("POST", "/user/repos", payload)

    def generate_from_template(self, template_full_name, name, owner=None, private=True, description=""):
        """Create a new repository from a template repo (the boilerplate flow)."""
        payload = {
            "name": name,
            "private": private,
            "description": description,
            "include_all_branches": False,
        }
        if owner:
            payload["owner"] = owner
        return self._request("POST", "/repos/%s/generate" % template_full_name, payload)

    # --- file contents ---
    def get_content(self, full_name, path, ref=None):
        """The file at ``path`` (decoded text + blob sha), or None when absent."""
        import base64

        try:
            data = self._request(
                "GET",
                "/repos/%s/contents/%s" % (full_name, path),
                params={"ref": ref} if ref else None,
            )
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        content = data.get("content") or ""
        text = base64.b64decode(content.encode("ascii")).decode("utf-8") if content else ""
        return {"text": text, "sha": data.get("sha"), "path": data.get("path")}

    def put_content(self, full_name, path, text, message, branch=None, sha=None):
        """Create or update the file at ``path`` (sha required to update)."""
        import base64

        payload = {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }
        if branch:
            payload["branch"] = branch
        if sha:
            payload["sha"] = sha
        return self._request("PUT", "/repos/%s/contents/%s" % (full_name, path), payload)

    # --- branches / refs ---
    def get_ref(self, full_name, ref):
        return self._request("GET", "/repos/%s/git/ref/%s" % (full_name, ref))

    def create_branch(self, full_name, new_branch, from_sha):
        return self._request(
            "POST",
            "/repos/%s/git/refs" % full_name,
            {"ref": "refs/heads/%s" % new_branch, "sha": from_sha},
        )

    # --- pull requests ---
    def create_pull(self, full_name, head, base, title, body=""):
        return self._request(
            "POST",
            "/repos/%s/pulls" % full_name,
            {"head": head, "base": base, "title": title, "body": body},
        )

    def get_pull(self, full_name, number):
        return self._request("GET", "/repos/%s/pulls/%s" % (full_name, number))

    # --- issues ---
    def create_issue(self, full_name, title, body="", labels=None):
        payload = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return self._request("POST", "/repos/%s/issues" % full_name, payload)

    def get_issue(self, full_name, number):
        return self._request("GET", "/repos/%s/issues/%s" % (full_name, number))


# --------------------------------------------------------------------------- #
# GitHub App auth (sign-as-the-app, mint installation tokens, manifest, hooks) #
# --------------------------------------------------------------------------- #
def app_jwt(app_id, private_key_pem):
    """Mint a short-lived RS256 JWT signed with the App private key (auth as App)."""
    import jwt  # PyJWT (RS256 needs `cryptography`); declared in external_dependencies

    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": str(app_id)}
    token = jwt.encode(payload, private_key_pem, algorithm="RS256")
    # PyJWT >= 2 returns str; older returns bytes.
    return token.decode() if isinstance(token, bytes) else token


def convert_manifest(code, requests, api_root=API_ROOT):
    """Exchange a manifest-flow temporary code for the created App's credentials.

    Returns id, slug, pem (private key), webhook_secret, client_id, client_secret,
    html_url, owner — everything we need to drive the App afterwards.
    """
    resp = requests.post(
        "%s/app-manifests/%s/conversions" % (api_root.rstrip("/"), code),
        headers={"Accept": "application/vnd.github+json", "User-Agent": "adomi-platform-management"},
        timeout=30,
    )
    if resp.status_code < 200 or resp.status_code >= 300:
        raise GitHubError(resp.status_code, (resp.text or resp.reason)[:200])
    return resp.json()


def verify_webhook_signature(secret, body_bytes, signature_header):
    """Validate an X-Hub-Signature-256 header (HMAC-SHA256 of the raw body)."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    digest = hmac.new(
        secret.encode() if isinstance(secret, str) else secret, body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest("sha256=" + digest, signature_header)


class GitHubAppClient:
    """App-level client (authenticated with the App JWT, not an installation)."""

    def __init__(self, jwt_token, requests, api_root=API_ROOT):
        self._jwt = jwt_token
        self._requests = requests
        self._api_root = (api_root or API_ROOT).rstrip("/")

    def _request(self, method, path, payload=None, params=None):
        resp = self._requests.request(
            method,
            self._api_root + path,
            json=payload,
            params=params,
            headers={
                "Authorization": "Bearer %s" % self._jwt,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "adomi-platform-management",
            },
            timeout=30,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            message = ""
            try:
                message = (resp.json() or {}).get("message", "")
            except Exception:  # noqa: BLE001
                message = resp.text[:200]
            raise GitHubError(resp.status_code, message or resp.reason)
        return resp.json() if resp.content else {}

    def app(self):
        return self._request("GET", "/app")

    def list_installations(self):
        return self._request("GET", "/app/installations", params={"per_page": 100})

    def get_installation(self, installation_id):
        return self._request("GET", "/app/installations/%s" % installation_id)

    def create_installation_token(self, installation_id):
        """Mint a ~1h installation token (returns token, expires_at, permissions)."""
        return self._request("POST", "/app/installations/%s/access_tokens" % installation_id)
