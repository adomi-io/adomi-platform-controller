"""Tests for environment-driven configuration."""

from __future__ import annotations

import pytest

from adomi_platform_controller.config import AuthMode, Config


def test_defaults_match_provisioner():
    cfg = Config()
    assert cfg.auth_mode is AuthMode.TOKEN
    assert cfg.kv_mount == "secret"
    assert cfg.openbao_addr.endswith(":8200")
    assert cfg.authentik_token_key == "bootstrap-token"
    assert cfg.cluster_secret_store == "openbao"


def test_from_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENBAO_AUTH_MODE", "kubernetes")
    monkeypatch.setenv("CLUSTER_SECRET_STORE", "mystore")
    monkeypatch.setenv("AUTHENTIK_ADDR", "http://authentik.example")

    cfg = Config.from_env()
    assert cfg.auth_mode is AuthMode.KUBERNETES
    assert cfg.cluster_secret_store == "mystore"
    assert cfg.authentik_addr == "http://authentik.example"


def test_blank_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENBAO_KV_MOUNT", "")
    assert Config.from_env().kv_mount == "secret"
