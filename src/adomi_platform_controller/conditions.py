"""Status-condition helpers shared by every platform CRD.

The convention is a single ``Ready`` condition per resource whose reason and
message explain the latest reconcile outcome. The helpers write the condition
list onto a Kopf ``patch`` object, preserving ``lastTransitionTime`` when only
the message changes, following the Kubernetes status-condition convention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# The canonical condition type carried by every platform resource.
TYPE_READY = "Ready"

# Standard reasons used across the controllers (CamelCase per API conventions).
REASON_RECONCILED = "Reconciled"
REASON_RECONCILING = "Reconciling"
REASON_BACKEND_ERROR = "BackendError"
REASON_DEPENDENCY_NOT_MET = "DependencyNotMet"
REASON_INVALID_SPEC = "InvalidSpec"


def _now() -> str:
    """RFC3339 timestamp in UTC with a trailing Z, as Kubernetes expects."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _upsert(
    existing: list[dict[str, Any]],
    status: str,
    reason: str,
    message: str,
    observed_generation: int,
) -> list[dict[str, Any]]:
    conds = [dict(c) for c in (existing or [])]

    for cond in conds:
        if cond.get("type") == TYPE_READY:
            if cond.get("status") != status:
                cond["lastTransitionTime"] = _now()

            cond["status"] = status
            cond["reason"] = reason
            cond["message"] = message
            cond["observedGeneration"] = observed_generation

            return conds

    conds.append(
        {
            "type": TYPE_READY,
            "status": status,
            "reason": reason,
            "message": message,
            "observedGeneration": observed_generation,
            "lastTransitionTime": _now(),
        }
    )
    return conds


def _conditions(status: dict[str, Any] | None) -> list[dict[str, Any]]:
    return list((status or {}).get("conditions") or [])


def mark_ready(patch: Any, status: dict[str, Any] | None, message: str, generation: int) -> None:
    """Set Ready=True with the standard Reconciled reason on the patch."""
    patch.status["conditions"] = _upsert(
        _conditions(status),
        "True",
        REASON_RECONCILED,
        message or "Resource reconciled successfully",
        generation,
    )

    patch.status["observedGeneration"] = generation


def mark_not_ready(
    patch: Any,
    status: dict[str, Any] | None,
    reason: str,
    message: str,
    generation: int,
) -> None:
    """Set Ready=False with the given reason and message on the patch."""
    patch.status["conditions"] = _upsert(_conditions(status), "False", reason, message, generation)

    patch.status["observedGeneration"] = generation
