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
