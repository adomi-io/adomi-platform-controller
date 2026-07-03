"""Git persistence: commit CR manifests to a customer's client repo.

``base`` defines the backend-agnostic contract; ``forgejo`` is the in-cluster
implementation. The API depends only on the :class:`~.base.GitWriter` protocol, so a
GitHub (or other) backend can be added without touching the service or routers.
"""

from .base import GitError, GitWriter, Readiness, MODE_COMMIT, MODE_PR  # noqa: F401
from .forgejo import ForgejoWriter  # noqa: F401
