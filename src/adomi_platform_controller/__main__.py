"""Run the operator with ``python -m adomi_platform_controller``.

The container image instead invokes ``kopf run`` directly (see Dockerfile), which
is the conventional way to start a Kopf operator; this module is a convenience
wrapper for local runs.
"""

from __future__ import annotations

import kopf

# Registers the @kopf.on.* handlers and the startup hook.
from . import operator  # noqa: F401


def main() -> None:
    kopf.run(
        clusterwide=True,
        standalone=True,
        liveness_endpoint="http://0.0.0.0:8080/healthz",
    )


if __name__ == "__main__":
    main()
