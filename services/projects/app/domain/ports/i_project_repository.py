from __future__ import annotations

import uuid
from typing import Protocol

from ..models import Project


class IProjectRepository(Protocol):
    """Port for project persistence.

    All methods are async so implementations can use either SQLAlchemy
    async sessions (production) or in-memory dicts (tests).
    """

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        """Return the project with the given id, or None if it does not exist."""
        ...

    async def get_by_user(self, user_id: str) -> list[Project]:
        """Return all projects owned by `user_id`, ordered by created_at desc."""
        ...

    async def create(self, project: Project) -> Project:
        """Persist a new project and return it with server-generated fields
        (id, created_at, updated_at) populated."""
        ...

    async def update(self, project: Project) -> Project:
        """Flush changes on an already-tracked project and return it with
        updated_at refreshed."""
        ...

    async def delete(self, project_id: uuid.UUID) -> None:
        """Remove the project.  No-op if it does not exist."""
        ...
