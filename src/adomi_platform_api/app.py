"""FastAPI application factory — the platform front door.

Resource routers live under ``/v1`` (see :mod:`.routers`); each write builds a
custom resource and commits it to the customer's client git repo. ``/readyz`` checks
the git backend is reachable so the deployment only takes traffic once it can write.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, status

from . import __version__
from .deps import check_backend_ready
from .git import Readiness
from .models import Health
from .routers import api_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Adomi Platform API",
        version=__version__,
        summary="Manage platform objects (clients, environments, applications, ...) as git-backed intent.",
    )
    app.include_router(api_router)

    @app.get("/healthz", response_model=Health, tags=["meta"])
    def healthz() -> Health:
        return Health()

    @app.get("/readyz", response_model=Health, tags=["meta"])
    def readyz(readiness: Readiness = Depends(check_backend_ready)) -> Health:
        if not readiness.ok:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, readiness.detail)

        return Health()

    return app


app = create_app()
