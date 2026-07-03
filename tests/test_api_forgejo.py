"""Tests for the Forgejo git writer (no network)."""

from __future__ import annotations

import base64
import json

import pytest

from adomi_platform_api.git import MODE_PR, GitError
from adomi_platform_api.git.forgejo import ForgejoWriter


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _StubSession:
    """httpx.Client-shaped stub; picks the longest matching (method, frag) rule."""

    def __init__(self, rules):
        self.calls = []
        self.rules = rules

    def request(self, method, url, headers=None, content=None, timeout=None):
        body = json.loads(content.decode("utf-8")) if content else None
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        best = None
        for (m, frag), resp in self.rules:
            if m == method and frag in url and (best is None or len(frag) > len(best[0])):
                best = (frag, resp)
        return best[1] if best else _Resp(500, text=f"no rule for {method} {url}")


def _writer(session, owner="clients"):
    return ForgejoWriter("https://git.example.com", "tok", owner, session=session)


def test_create_when_absent_uses_post():
    # A new file must be created with POST — Forgejo rejects a sha-less PUT
    # with 422 "[SHA]: Required".
    session = _StubSession(
        [
            (("GET", "repos/clients/acme"), _Resp(200, {"name": "acme"})),
            (("GET", "contents/clients/acme.yaml"), _Resp(404)),
            (("POST", "contents/clients/acme.yaml"), _Resp(201, {"content": {"sha": "a"}})),
        ]
    )
    res = _writer(session).apply_manifest("acme", "clients/acme.yaml", "kind: Client\n", "m")
    post = [c for c in session.calls if "contents/" in c["url"] and c["method"] == "POST"][0]
    assert "sha" not in post["body"]
    assert base64.b64decode(post["body"]["content"]).decode() == "kind: Client\n"
    assert res["committed"] and post["headers"]["Authorization"] == "token tok"
    assert not [c for c in session.calls if c["method"] == "PUT"]


def test_update_when_present_uses_put_with_sha():
    session = _StubSession(
        [
            (("GET", "repos/clients/acme"), _Resp(200, {"name": "acme"})),
            (("GET", "contents/clients/acme.yaml"), _Resp(200, {"sha": "oldsha"})),
            (("PUT", "contents/clients/acme.yaml"), _Resp(200, {"content": {"sha": "new"}})),
        ]
    )
    res = _writer(session).apply_manifest("acme", "clients/acme.yaml", "kind: Client\n", "m")
    put = [c for c in session.calls if c["method"] == "PUT"][0]
    assert put["body"]["sha"] == "oldsha"
    assert res["committed"]


def test_pr_mode_opens_pull():
    session = _StubSession(
        [
            (("GET", "repos/clients/acme"), _Resp(200, {"name": "acme"})),
            (("GET", "contents/applications/erp.yaml"), _Resp(404)),
            (("POST", "contents/applications/erp.yaml"), _Resp(201, {"content": {"sha": "x"}})),
            (("POST", "pulls"), _Resp(201, {"number": 7})),
        ]
    )
    res = _writer(session).apply_manifest("acme", "applications/erp.yaml", "x\n", "m", mode=MODE_PR)
    post = [c for c in session.calls if "contents/" in c["url"] and c["method"] == "POST"][0]
    assert post["body"]["new_branch"] == "adomi/applications-erp"
    assert res["pr"] == {"number": 7}


def test_delete_absent_is_noop():
    session = _StubSession([(("GET", "contents/x/y.yaml"), _Resp(404))])
    w = _writer(session)
    assert w.delete_manifest("acme", "x/y.yaml", "m") == {"deleted": False, "reason": "absent"}
    assert not [c for c in session.calls if c["method"] == "DELETE"]


def test_ensure_repo_creates_on_404():
    session = _StubSession(
        [
            (("GET", "repos/clients/new"), _Resp(404)),
            (("POST", "orgs/clients/repos"), _Resp(201, {"name": "new"})),
        ]
    )
    assert _writer(session).ensure_repo("new") is True


def test_check_ready():
    up = _StubSession([(("GET", "orgs/clients"), _Resp(200, {"username": "clients"}))])
    assert _writer(up).check_ready().ok
    down = _StubSession([(("GET", "orgs/clients"), _Resp(403, text="forbidden"))])
    r = _writer(down).check_ready()
    assert not r.ok and "403" in r.detail


def test_missing_config_raises():
    with pytest.raises(GitError):
        ForgejoWriter("", "tok", "clients", session=_StubSession([]))
    with pytest.raises(GitError):
        ForgejoWriter("https://x", "", "clients", session=_StubSession([]))
