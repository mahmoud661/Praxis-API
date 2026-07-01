from __future__ import annotations

import uuid

from ...application.projects_service import ProjectsService
from ...domain.models import Project


class ProjectsController:
    """Thin orchestration layer between the route handlers and the service.

    Routes are responsible for HTTP concerns (request parsing, response
    serialisation, status codes).  This controller holds no HTTP primitives
    — it is testable without a running ASGI server.
    """

    def __init__(self, projects_service: ProjectsService) -> None:
        self._service = projects_service

    async def list_projects(self, user_id: str) -> list[Project]:
        return await self._service.list_projects(user_id)

    async def get_project(self, project_id: uuid.UUID, user_id: str) -> Project:
        return await self._service.get_project(project_id, user_id)

    async def create_project(
        self,
        user_id: str,
        name: str,
        description: str | None,
        github_repo_url: str | None,
        github_token: str | None,
        setup_commands: list[str] | None = None,
        start_command: str | None = None,
        registered_ports: list[int] | None = None,
    ) -> Project:
        return await self._service.create_project(
            user_id=user_id,
            name=name,
            description=description,
            github_repo_url=github_repo_url,
            github_token=github_token,
            setup_commands=setup_commands,
            start_command=start_command,
            registered_ports=registered_ports,
        )

    async def update_project(
        self,
        project_id: uuid.UUID,
        user_id: str,
        **kwargs,
    ) -> Project:
        return await self._service.update_project(project_id, user_id, **kwargs)

    async def delete_project(self, project_id: uuid.UUID, user_id: str) -> None:
        await self._service.delete_project(project_id, user_id)

    async def assign_sandbox(
        self, project_id: uuid.UUID, user_id: str, sandbox_id: str
    ) -> Project:
        return await self._service.assign_sandbox(project_id, user_id, sandbox_id)
