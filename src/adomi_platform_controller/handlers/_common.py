"""Helpers shared by the reconcile handlers."""

from __future__ import annotations

from typing import Any, NoReturn

import kopf

from .. import conditions

# How long to wait before retrying when a prerequisite (e.g. a backend) is not
# yet ready.
DEPENDENCY_REQUEUE = 30  # seconds


def fail(
    patch: Any,
    status: dict[str, Any] | None,
    reason: str,
    message: str,
    generation: int,
    delay: float = DEPENDENCY_REQUEUE,
) -> NoReturn:
    """Record Ready=False and requeue after ``delay`` without a hard error.

    Raising ``kopf.TemporaryError`` reschedules the handler; the accumulated
    ``patch`` (status condition + observedGeneration) is still applied, so the
    resource reflects the not-ready reason. This avoids the noisy exponential
    backoff of an unhandled exception for expected transient conditions.
    """
    conditions.mark_not_ready(patch, status, reason, message, generation)
    raise kopf.TemporaryError(message, delay=delay)


class Reconciler:
    """Base for a CRD's reconciler.

    Subclasses set ``plural`` (and override ``GROUP`` for non-platform groups) and
    implement ``reconcile`` / ``finalize``. Thin ``@kopf.on.*`` functions in the
    module instantiate the reconciler once and delegate to it, so the Kopf
    registration stays explicit while the logic lives on the class.
    """

    GROUP = "platform.adomi.io"
    VERSION = "v1alpha1"
    MANAGED_BY = "adomi-platform-controller"
    plural: str = ""
