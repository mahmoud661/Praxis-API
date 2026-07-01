from __future__ import annotations

import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from ..domain.models import Project
from ..domain.ports.i_project_repository import IProjectRepository
from ..infrastructure.config.env import Settings


class ProjectsService:
    """Application service for the projects aggregate.

    Owns the encryption/decryption of GitHub tokens — the repository and
    route layers never see plaintext credentials.  The Fernet key is
    loaded once from settings at construction; rotating it requires a
    migration step outside this service's scope.
    """

    def __init__(
        self,
        project_repository: IProjectRepository,
        settings: Settings,
    ) -> None:
        self._repo = project_repository
        # Fernet requires a URL-safe base64-encoded 32-byte key.  Pydantic
        # settings validates the key is present; the Fernet constructor
        # raises ValueError at boot if the encoding is wrong, surfacing
        # misconfigs immediately rather than on the first encrypt call.
        self._fernet = Fernet(settings.encryption_key.encode())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _encrypt_token(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def _decrypt_token(self, encrypted: bytes) -> str:
        try:
            return self._fernet.decrypt(encrypted).decode()
        except InvalidToken as exc:
            # This should never happen in normal operation — it would mean
            # the key changed without a migration.  Surface it as a 500 so
            # ops see it immediately in logs/traces.
            raise HTTPException(
                status_code=500,
                detail="Failed to decrypt GitHub token — encryption key mismatch.",
            ) from exc

    def _assert_ownership(self, project: Project | None, user_id: str) -> Project:
        """Return `project` if it exists and is owned by `user_id`, else 404.

        We deliberately return 404 (not 403) to avoid leaking the existence
        of projects owned by other users.
        """
        if project is None or project.user_id != user_id:
            raise HTTPException(status_code=404, detail="Project not found.")
        return project

    # ------------------------------------------------------------------
    # IProjectService implementation
    # ------------------------------------------------------------------

    async def list_projects(self, user_id: str) -> list[Project]:
        return await self._repo.get_by_user(user_id)

    async def get_project(self, project_id: uuid.UUID, user_id: str) -> Project:
        project = await self._repo.get_by_id(project_id)
        return self._assert_ownership(project, user_id)

    async def create_project(
        self,
        user_id: str,
        name: str,
        description: str | None,
        github_repo_url: str | None,
        github_token: str | None,
    ) -> Project:
        encrypted_token: bytes | None = None
        if github_token:
            encrypted_token = self._encrypt_token(github_token)

        project = Project(
            id=uuid.uuid4(),
            user_id=user_id,
            name=name,
            description=description,
            github_repo_url=github_repo_url,
            github_encrypted_token=encrypted_token,
        )
        return await self._repo.create(project)

    async def update_project(
        self,
        project_id: uuid.UUID,
        user_id: str,
        **kwargs: Any,
    ) -> Project:
        project = await self._repo.get_by_id(project_id)
        self._assert_ownership(project, user_id)

        # Apply only the fields the caller explicitly provided.
        # `github_token` is special — it gets encrypted before storage.
        for field, value in kwargs.items():
            if field == "github_token":
                if value is not None:
                    project.github_encrypted_token = self._encrypt_token(value)
                else:
                    # Caller explicitly cleared the token.
                    project.github_encrypted_token = None
            elif hasattr(project, field):
                setattr(project, field, value)

        return await self._repo.update(project)

    async def delete_project(self, project_id: uuid.UUID, user_id: str) -> None:
        project = await self._repo.get_by_id(project_id)
        self._assert_ownership(project, user_id)
        await self._repo.delete(project_id)

    async def assign_sandbox(
        self, project_id: uuid.UUID, user_id: str, sandbox_id: str
    ) -> Project:
        project = await self._repo.get_by_id(project_id)
        self._assert_ownership(project, user_id)
        project.sandbox_id = sandbox_id
        return await self._repo.update(project)
