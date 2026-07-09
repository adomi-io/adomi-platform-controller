"""A client's built container images (Harbor-backed, read-only).

Every from-source build lands in one Harbor project as ``<client>-<app>``
(the controller's built_image_ref), so a client's images are the project
repositories carrying its name prefix. Image lifecycle stays with the build
pipeline and Harbor retention — the portal only lists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import Settings, get_settings
from ..deps import get_registry
from ..models import ImageArtifact
from ..registry import HarborRegistry, RegistryError

router = APIRouter(prefix="/clients/{client}/images", tags=["images"])


def _artifact(settings: Settings, client: str, repository: str, art: dict) -> ImageArtifact:
    tags = [t.get("name") for t in art.get("tags") or [] if t.get("name")]
    host = settings.harbor_host or settings.harbor_url.split("://", 1)[-1]
    ref_tag = tags[0] if tags else (art.get("digest") or "")[:19]

    return ImageArtifact(
        repository=repository,
        application=repository.removeprefix(f"{client}-"),
        image=f"{host}/{settings.harbor_project}/{repository}:{ref_tag}",
        tags=tags,
        digest=art.get("digest") or "",
        size_bytes=int(art.get("size") or 0),
        pushed_at=art.get("push_time") or "",
    )


@router.get("", response_model=list[ImageArtifact])
def list_images(
    client: str,
    registry: HarborRegistry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
) -> list[ImageArtifact]:
    """Tagged artifacts of every repository built for the client, newest first."""
    try:
        repositories = registry.list_repositories(settings.harbor_project, f"{client}-")

        images = [
            _artifact(settings, client, repo, art)
            for repo in repositories
            for art in registry.list_artifacts(settings.harbor_project, repo)
            if art.get("tags")  # untagged artifacts are build/GC leftovers
        ]
    except RegistryError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return sorted(images, key=lambda i: i.pushed_at, reverse=True)
