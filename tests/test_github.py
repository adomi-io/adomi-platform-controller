"""Tests for the GitHub client's pure helpers."""

from __future__ import annotations

from adomi_platform_controller import github


def test_preview_comment_body_carries_marker():
    body = github.preview_comment_body("https://pr-42.erp.acme.adomi.io")
    assert github.PREVIEW_COMMENT_MARKER in body
    assert "https://pr-42.erp.acme.adomi.io" in body


def test_status_payload():
    p = github.status_payload("success", "https://pr-42...", "Preview environment deployed")
    assert p["state"] == "success"
    assert p["target_url"] == "https://pr-42..."
    assert p["context"] == github.STATUS_CONTEXT
    assert len(p["description"]) <= 140


def test_status_payload_truncates_description():
    p = github.status_payload("failure", "", "x" * 300)
    assert len(p["description"]) == 140


# --- GitHub URL detection -------------------------------------------------------


def test_is_github_url_variants():
    assert github.is_github_url("https://github.com/acme/erp")
    assert github.is_github_url("https://github.com/acme/erp.git")
    assert github.is_github_url("git@github.com:acme/erp.git")
    assert github.is_github_url("https://user@github.com/acme/erp")
    assert not github.is_github_url("https://gitlab.com/acme/erp")
    assert not github.is_github_url("http://forgejo-http.forgejo.svc:3000/clients/acme.git")
    assert not github.is_github_url("")


# --- App auth: JWT + installation tokens ----------------------------------------


def _rsa_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_app_jwt_claims():
    import jwt as pyjwt

    auth = github.GitHubAppAuth("123456", _rsa_pem())
    claims = pyjwt.decode(auth._app_jwt(), options={"verify_signature": False})

    assert claims["iss"] == "123456"
    assert claims["iat"] < claims["exp"]
    # GitHub rejects exp more than 10 minutes out.
    assert claims["exp"] - claims["iat"] <= 10 * 60


def test_installation_token_minting_and_cache(monkeypatch):
    calls = []

    def fake_request(api, token, method, path, body=None):
        calls.append((method, path, body))

        if path.endswith("/installation"):
            return {"id": 42}

        return {"token": "ghs_minted"}

    monkeypatch.setattr(github, "_api_request", fake_request)
    monkeypatch.setattr(github.GitHubAppAuth, "_app_jwt", lambda self: "app.jwt")

    auth = github.GitHubAppAuth("123456", "unused-pem")

    assert auth.installation_token_for("acme", "erp") == "ghs_minted"
    assert calls[0] == ("GET", "/repos/acme/erp/installation", None)
    assert calls[1][0:2] == ("POST", "/app/installations/42/access_tokens")
    # Scoped to the one repository, read-only contents.
    assert calls[1][2] == {"repositories": ["erp"], "permissions": {"contents": "read"}}

    # Second call is served from the cache — no further API traffic.
    assert auth.installation_token_for("acme", "erp") == "ghs_minted"
    assert len(calls) == 2


def test_installation_token_not_installed(monkeypatch):
    import urllib.error

    import pytest

    def fake_request(api, token, method, path, body=None):
        raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)

    monkeypatch.setattr(github, "_api_request", fake_request)
    monkeypatch.setattr(github.GitHubAppAuth, "_app_jwt", lambda self: "app.jwt")

    auth = github.GitHubAppAuth("123456", "unused-pem")

    with pytest.raises(github.GitHubAppError):
        auth.installation_token_for("acme", "erp")


def test_app_auth_reuses_instance_until_key_rotates():
    github._app_auth_cache.clear()

    first = github.app_auth("123456", "pem-a")
    again = github.app_auth("123456", "pem-a")
    rotated = github.app_auth("123456", "pem-b")

    assert again is first
    assert rotated is not first
