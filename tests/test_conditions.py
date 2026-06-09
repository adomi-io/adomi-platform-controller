"""Tests for the Ready status-condition helpers."""

from __future__ import annotations

from adomi_platform_controller import conditions


class FakePatch:
    """Minimal stand-in for kopf's patch object (patch.status is a dict)."""

    def __init__(self) -> None:
        self.status: dict = {}


def test_mark_ready_sets_condition_and_generation():
    patch = FakePatch()
    conditions.mark_ready(patch, {"conditions": []}, "all good", 7)

    cond = patch.status["conditions"][0]
    assert cond["type"] == "Ready"
    assert cond["status"] == "True"
    assert cond["reason"] == conditions.REASON_RECONCILED
    assert cond["message"] == "all good"
    assert cond["observedGeneration"] == 7
    assert patch.status["observedGeneration"] == 7


def test_last_transition_time_preserved_when_status_unchanged():
    first = FakePatch()
    conditions.mark_ready(first, {"conditions": []}, "v1", 1)
    ltt = first.status["conditions"][0]["lastTransitionTime"]

    second = FakePatch()
    conditions.mark_ready(second, first.status, "v2", 2)
    assert second.status["conditions"][0]["lastTransitionTime"] == ltt
    assert second.status["conditions"][0]["message"] == "v2"


def test_mark_not_ready_uses_given_reason():
    patch = FakePatch()
    conditions.mark_not_ready(
        patch, {"conditions": []}, conditions.REASON_DEPENDENCY_NOT_MET, "flows missing", 3
    )
    cond = patch.status["conditions"][0]
    assert cond["status"] == "False"
    assert cond["reason"] == conditions.REASON_DEPENDENCY_NOT_MET
    assert cond["message"] == "flows missing"
