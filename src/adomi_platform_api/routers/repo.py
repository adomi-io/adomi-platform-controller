"""Read-only views of a client's infrastructure repository (the git panel).

The portal shows the repo's files and recent commits right on the customer page,
so the GitOps flow is visible where the intent is edited. Reads go straight to
the git backend; nothing here mutates state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import get_writer
from ..git import GitError, GitWriter
from ..models import RepoCommit, RepoFile

router = APIRouter(prefix="/clients/{client}/repo", tags=["repo"])


@router.get("/tree", response_model=list[RepoFile])
def repo_tree(
    client: str,
    ref: str | None = Query(default=None, description="Branch / tag / commit (default branch)."),
    writer: GitWriter = Depends(get_writer),
) -> list[RepoFile]:
    try:
        return [RepoFile(**e) for e in writer.list_tree(client, ref=ref)]
    except GitError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/commits", response_model=list[RepoCommit])
def repo_commits(
    client: str,
    limit: int = Query(default=10, ge=1, le=50),
    ref: str | None = Query(default=None, description="Branch / tag / commit (default branch)."),
    writer: GitWriter = Depends(get_writer),
) -> list[RepoCommit]:
    try:
        return [RepoCommit(**c) for c in writer.list_commits(client, limit=limit, ref=ref)]
    except GitError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
