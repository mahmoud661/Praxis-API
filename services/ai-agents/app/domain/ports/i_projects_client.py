"""DI token `"IProjectsClient"` — port for the projects service.

Lets the runner fetch a linked project's context (repo URL, sandbox id)
so it can prime the agent's first turn. Kept minimal on purpose — the
ai-agents service only ever needs to READ a project by id; all project
CRUD lives in the projects service behind the gateway.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """Just the fields the agent needs to know about a project."""

    id: str
    name: str
    github_repo_url: str | None = None
    sandbox_id: str | None = None


class IProjectsClient(Protocol):
    """Port for the projects service HTTP adapter."""

    async def get_project(
        self, *, project_id: str, owner_id: str
    ) -> ProjectContext | None:
        """Fetch a project by id, scoped to `owner_id` (forwarded as the
        `X-User-Id` header). Returns `None` when the project is missing,
        not owned by the user, or the service is unavailable — the caller
        treats project context as best-effort and must never fail a run
        because of it."""
        ...
