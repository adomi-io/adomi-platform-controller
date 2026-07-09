"""Tests for the Harbor admin client and the pull-secret delivery shape."""

from __future__ import annotations

import base64
import json
import urllib.error

import pytest

from adomi_platform_controller import externalsecrets, harbor


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://harbor", code, "boom", {}, None)


def test_client_sends_basic_auth_and_api_prefix(monkeypatch):
    calls = []

    def fake_request(base, auth, method, path, body=None, headers=None):
        calls.append((base, auth, method, path, body))
        return None

    monkeypatch.setattr(harbor, "_api_request", fake_request)
    harbor.HarborClient("http://harbor-core.harbor.svc:80/", "admin", "pw").ensure_project(
        "previews"
    )

    base, auth, method, path, body = calls[0]
    assert base == "http://harbor-core.harbor.svc:80"  # trailing slash stripped
    assert base64.b64decode(auth).decode() == "admin:pw"
    assert (method, path) == ("POST", "/projects")
    assert body == {"project_name": "previews", "metadata": {"public": "false"}}


def test_ensure_project_tolerates_conflict(monkeypatch):
    def fake_request(base, auth, method, path, body=None, headers=None):
        raise _http_error(409)

    monkeypatch.setattr(harbor, "_api_request", fake_request)
    harbor.HarborClient("http://h", "admin", "pw").ensure_project("previews")  # no raise


def test_ensure_project_raises_on_other_errors(monkeypatch):
    def fake_request(base, auth, method, path, body=None, headers=None):
        raise _http_error(401)

    monkeypatch.setattr(harbor, "_api_request", fake_request)

    with pytest.raises(harbor.HarborError, match="HTTP 401"):
        harbor.HarborClient("http://h", "admin", "pw").ensure_project("previews")


def test_ensure_pull_robot_recreates_and_returns_credentials(monkeypatch):
    calls = []

    def fake_request(base, auth, method, path, body=None, headers=None):
        calls.append((method, path))

        if method == "GET":
            # Name-based project paths 404 without this header (Harbor parses
            # the segment as an integer id otherwise).
            assert headers == {"X-Is-Resource-Name": "true"}
            # An existing pull robot (stale: its secret is unrecoverable) plus an
            # unrelated robot that must be left alone.
            return [
                {"id": 7, "name": "robot$previews+pull"},
                {"id": 9, "name": "robot$previews+ci"},
            ]

        if method == "POST":
            assert body["level"] == "project"
            assert body["permissions"][0]["namespace"] == "previews"
            assert body["permissions"][0]["access"] == [
                {"resource": "repository", "action": "pull"}
            ]
            return {"name": "robot$previews+pull", "secret": "s3cret"}

        return None

    monkeypatch.setattr(harbor, "_api_request", fake_request)
    username, secret = harbor.HarborClient("http://h", "admin", "pw").ensure_pull_robot("previews")

    assert (username, secret) == ("robot$previews+pull", "s3cret")
    assert ("DELETE", "/robots/7") in calls
    assert ("DELETE", "/robots/9") not in calls


def test_ensure_pull_robot_requires_returned_secret(monkeypatch):
    def fake_request(base, auth, method, path, body=None, headers=None):
        return {"name": "robot$previews+pull"} if method == "POST" else []

    monkeypatch.setattr(harbor, "_api_request", fake_request)

    with pytest.raises(harbor.HarborError, match="no credentials"):
        harbor.HarborClient("http://h", "admin", "pw").ensure_pull_robot("previews")


# --- pull-secret ExternalSecret shape --------------------------------------------


def test_dockerconfigjson_template_renders_valid_json_shape():
    tpl = externalsecrets.dockerconfigjson_template("harbor.example.com")
    # Substitute the Go-template placeholders and confirm the rest is valid JSON.
    rendered = (
        tpl.replace("{{ .username }}", "robot$previews+pull")
        .replace("{{ .password }}", "pw")
        .replace('{{ printf "%s:%s" .username .password | b64enc }}', "auth")
    )
    cfg = json.loads(rendered.replace("robot$previews+pull", "robot"))

    assert "harbor.example.com" in cfg["auths"]
    assert cfg["auths"]["harbor.example.com"]["username"] == "robot"


def test_pull_secret_manifest_is_typed_dockerconfigjson_only():
    es = externalsecrets.ExternalSecret(
        name="harbor-pull",
        namespace="acme-production",
        store_name="openbao",
        remote_path="harbor-pull",
        data_map={"username": "username", "password": "password"},
        template_type="kubernetes.io/dockerconfigjson",
        template_data={
            ".dockerconfigjson": externalsecrets.dockerconfigjson_template("harbor.example.com"),
        },
    )
    template = es.manifest()["spec"]["target"]["template"]

    assert template["type"] == "kubernetes.io/dockerconfigjson"
    # With an explicit type the template fully defines the payload — the fetched
    # username/password are inputs only, never extra Secret keys.
    assert list(template["data"]) == [".dockerconfigjson"]
