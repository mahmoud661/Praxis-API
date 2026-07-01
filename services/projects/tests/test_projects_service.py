"""Unit tests for ProjectsService.

Uses in-memory fakes for the repository and a real Fernet key so no
external services (Postgres, Kafka) are required.  The Dockerfile gate
runs these during the image build.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from app.application.projects_service import ProjectsService
from app.domain.models import Project
from app.infrastructure.config.env import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(key: str | None = None) -> Settings:
    """Build a Settings instance with a valid Fernet key."""
    fernet_key = key or Fernet.generate_key().decode()
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        encryption_key=fernet_key,
    )


def _make_project(user_id: str = "user-1", name: str = "My Project") -> Project:
    now = datetime.now(timezone.utc)
    return Project(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        created_at=now,
        updated_at=now,
    )


class FakeRepo:
    """In-memory IProjectRepository fake."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, Project] = {}

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        return self._store.get(project_id)

    async def get_by_user(self, user_id: str) -> list[Project]:
        return [p for p in self._store.values() if p.user_id == user_id]

    async def create(self, project: Project) -> Project:
        self._store[project.id] = project
        return project

    async def update(self, project: Project) -> Project:
        self._store[project.id] = project
        return project

    async def delete(self, project_id: uuid.UUID) -> None:
        self._store.pop(project_id, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings() -> Settings:
    return _make_settings()


@pytest.fixture()
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture()
def service(repo: FakeRepo, settings: Settings) -> ProjectsService:
    return ProjectsService(project_repository=repo, settings=settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_project_no_token(service: ProjectsService) -> None:
    project = await service.create_project(
        user_id="u1",
        name="P1",
        description=None,
        github_repo_url=None,
        github_token=None,
    )
    assert project.name == "P1"
    assert project.github_encrypted_token is None


@pytest.mark.asyncio
async def test_create_project_encrypts_token(service: ProjectsService, settings: Settings) -> None:
    project = await service.create_project(
        user_id="u1",
        name="P2",
        description=None,
        github_repo_url=None,
        github_token="ghp_secret",
    )
    assert project.github_encrypted_token is not None
    # Encrypted bytes must differ from the plaintext.
    assert project.github_encrypted_token != b"ghp_secret"
    # Decryption round-trip via the same key must yield the original.
    fernet = Fernet(settings.encryption_key.encode())
    assert fernet.decrypt(project.github_encrypted_token).decode() == "ghp_secret"


@pytest.mark.asyncio
async def test_list_projects_scoped_to_user(service: ProjectsService) -> None:
    await service.create_project("u1", "A", None, None, None)
    await service.create_project("u1", "B", None, None, None)
    await service.create_project("u2", "C", None, None, None)

    u1_projects = await service.list_projects("u1")
    assert len(u1_projects) == 2
    assert all(p.user_id == "u1" for p in u1_projects)


@pytest.mark.asyncio
async def test_get_project_not_found_raises_404(service: ProjectsService) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await service.get_project(uuid.uuid4(), "u1")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_project_wrong_owner_raises_404(service: ProjectsService) -> None:
    from fastapi import HTTPException

    project = await service.create_project("u1", "Mine", None, None, None)
    with pytest.raises(HTTPException) as exc_info:
        await service.get_project(project.id, "u2")  # wrong user
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_project_name(service: ProjectsService) -> None:
    project = await service.create_project("u1", "Old Name", None, None, None)
    updated = await service.update_project(project.id, "u1", name="New Name")
    assert updated.name == "New Name"


@pytest.mark.asyncio
async def test_update_project_clears_token(
    service: ProjectsService,
) -> None:
    project = await service.create_project("u1", "P", None, None, "tok")
    assert project.github_encrypted_token is not None

    updated = await service.update_project(project.id, "u1", github_token=None)
    assert updated.github_encrypted_token is None


@pytest.mark.asyncio
async def test_delete_project(service: ProjectsService, repo: FakeRepo) -> None:
    project = await service.create_project("u1", "ToDelete", None, None, None)
    await service.delete_project(project.id, "u1")
    assert await repo.get_by_id(project.id) is None


@pytest.mark.asyncio
async def test_delete_project_wrong_owner_raises_404(service: ProjectsService) -> None:
    from fastapi import HTTPException

    project = await service.create_project("u1", "Mine", None, None, None)
    with pytest.raises(HTTPException) as exc_info:
        await service.delete_project(project.id, "u2")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_assign_sandbox(service: ProjectsService) -> None:
    project = await service.create_project("u1", "P", None, None, None)
    updated = await service.assign_sandbox(project.id, "u1", "sandbox-abc")
    assert updated.sandbox_id == "sandbox-abc"
