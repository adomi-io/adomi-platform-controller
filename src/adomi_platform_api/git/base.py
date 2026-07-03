"""The git-backend contract the API writes through."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

MODE_COMMIT = "commit"  # push straight to the default branch
MODE_PR = "pr"  # commit to a branch and open a pull request


class GitError(Exception):
    """A git-backend operation failed (network or non-2xx response)."""


@dataclass(frozen=True)
class Readiness:
    """Whether the git backend is reachable/writable (for /readyz)."""

    ok: bool
    detail: str = ""

    @classmethod
    def up(cls) -> Readiness:
        return cls(ok=True)

    @classmethod
    def down(cls, detail: str) -> Readiness:
        return cls(ok=False, detail=detail)


@runtime_checkable
class GitWriter(Protocol):
    """Commits and removes manifest files in a customer's client repo."""

    def apply_manifest(
        self, repo: str, path: str, content: str, message: str, *, mode: str = MODE_COMMIT
    ) -> dict:
        """Create or update ``path`` in ``repo`` with ``content`` (idempotent)."""
        ...

    def delete_manifest(
        self, repo: str, path: str, message: str, *, mode: str = MODE_COMMIT
    ) -> dict:
        """Remove ``path`` from ``repo`` (no-op if already absent)."""
        ...

    def check_ready(self) -> Readiness:
        """Report whether the backend is reachable and authenticated."""
        ...
