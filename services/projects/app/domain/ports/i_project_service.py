from __future__ import annotations

import uuid
from typing import Any, Protocol

from ..models import Project


class IProjectService(Protocol):
    """Port for the projects application service.

    All operations that mutate state enforce ownership: if `user_id`
    does not own `project_id`, the implementation raises HTTP 404 (not
    403, to avoid leaking existence).
    """

    async def list_projects(self, user_id: str) -> list[Project]:
        """Return all projects owned by `user_id`."""
        ...

    async def get_project(self, project_id: uuid.UUID, user_id: str) -> Project:
        """Return the project or raise HTTP 404 if not found / not owned."""
        ...

    async def create_project(
        self,
        user_id: str,
        name: str,
        description: str | None,
        github_repo_url: str | None,
        github_token: str | None,
    ) -> Project:
        """Create a new project.  `github_token` is encrypted at rest."""
        ...

    async def update_project(
        self,
        project_id: uuid.UUID,
        user_id: str,
        **kwargs: Any,
    ) -> Project:
        """Partially update the project.  Accepted keys mirror ProjectUpdate
        schema fields: name, description, github_repo_url, github_token."""
        ...

    async def delete_project(self, project_id: uuid.UUID, user_id: str) -> None:
        """Delete the project.  Raises HTTP 404 if not found / not owned."""
        ...

    async def assign_sandbox(
        self, project_id: uuid.UUID, user_id: str, sandbox_id: str
    ) -> Project:
        """Attach a sandbox identifier to the project and persist it."""
        ...
