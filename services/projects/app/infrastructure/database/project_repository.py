from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...domain.models import Project


class ProjectRepository:
    """SQLAlchemy async implementation of IProjectRepository.

    Each public method opens a fresh session from the factory, performs
    its work, commits (on mutations), and returns.  The session is
    scoped to the method call — no unit-of-work shared across requests.
    This matches the stateless request/response pattern of the route
    layer and avoids identity-map contamination across concurrent requests.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        async with self._factory() as session:
            result = await session.execute(
                select(Project).where(Project.id == project_id)
            )
            return result.scalar_one_or_none()

    async def get_by_user(self, user_id: str) -> list[Project]:
        async with self._factory() as session:
            result = await session.execute(
                select(Project)
                .where(Project.user_id == user_id)
                .order_by(Project.created_at.desc())
            )
            return list(result.scalars().all())

    async def create(self, project: Project) -> Project:
        async with self._factory() as session:
            session.add(project)
            await session.commit()
            await session.refresh(project)
            return project

    async def update(self, project: Project) -> Project:
        async with self._factory() as session:
            # Stamp updated_at explicitly.  SQLAlchemy's `onupdate` only
            # fires on `UPDATE` SQL generated through the ORM flush; since
            # we merge a detached object we set the timestamp ourselves.
            project.updated_at = datetime.now(timezone.utc)
            merged = await session.merge(project)
            await session.commit()
            await session.refresh(merged)
            return merged

    async def delete(self, project_id: uuid.UUID) -> None:
        async with self._factory() as session:
            await session.execute(
                delete(Project).where(Project.id == project_id)
            )
            await session.commit()
