"""Tests for build Secret construction."""

from __future__ import annotations

import base64

from adomi_platform_controller import buildsecrets


def test_dockerconfigjson():
    cfg = buildsecrets.dockerconfigjson("harbor.example.com", "admin", "s3cret")
    entry = cfg["auths"]["harbor.example.com"]
    assert entry["username"] == "admin"
    assert entry["password"] == "s3cret"
    # auth is base64("user:password").
    assert base64.b64decode(entry["auth"]).decode() == "admin:s3cret"


def test_dockerconfigjson_single_host():
    cfg = buildsecrets.dockerconfigjson("h", "u", "p")
    assert list(cfg["auths"].keys()) == ["h"]
