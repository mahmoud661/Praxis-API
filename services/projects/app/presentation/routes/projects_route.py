from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, Response, status

from ..controllers.projects_controller import ProjectsController
from ..schemas import ProjectCreate, ProjectOut, ProjectUpdate, SandboxAssign
from .base_route import BaseRoute


class ProjectsRoute(BaseRoute):
    """REST endpoints for the projects resource.

    All endpoints read the authenticated user's identity from the
    `X-User-Id` header.  The API gateway (or auth middleware upstream)
    is responsible for validating the JWT and injecting this header —
    the projects service treats it as trusted input.
    """

    path = "/projects"

    def __init__(self, projects_controller: ProjectsController) -> None:
        self._ctrl = projects_controller
        self.router = APIRouter(tags=["projects"])
        self._register()

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register(self) -> None:
        router = self.router

        @router.get("", response_model=list[ProjectOut], summary="List projects")
        async def list_projects(
            x_user_id: str = Header(..., alias="X-User-Id"),
        ) -> list[ProjectOut]:
            """Return all projects owned by the authenticated user."""
            projects = await self._ctrl.list_projects(user_id=x_user_id)
            return [ProjectOut.from_model(p) for p in projects]

        @router.post(
            "",
            response_model=ProjectOut,
            status_code=status.HTTP_201_CREATED,
            summary="Create project",
        )
        async def create_project(
            body: ProjectCreate,
            x_user_id: str = Header(..., alias="X-User-Id"),
        ) -> ProjectOut:
            """Create a new project for the authenticated user.

            If `github_token` is supplied it is encrypted with Fernet
            before being persisted — the plaintext is never stored.
            """
            project = await self._ctrl.create_project(
                user_id=x_user_id,
                name=body.name,
                description=body.description,
                github_repo_url=body.github_repo_url,
                github_token=body.github_token,
            )
            return ProjectOut.from_model(project)

        @router.get(
            "/{project_id}",
            response_model=ProjectOut,
            summary="Get project",
        )
        async def get_project(
            project_id: uuid.UUID,
            x_user_id: str = Header(..., alias="X-User-Id"),
        ) -> ProjectOut:
            """Return a single project.  404 if it does not exist or is not
            owned by the authenticated user."""
            project = await self._ctrl.get_project(
                project_id=project_id, user_id=x_user_id
            )
            return ProjectOut.from_model(project)

        @router.patch(
            "/{project_id}",
            response_model=ProjectOut,
            summary="Update project",
        )
        async def update_project(
            project_id: uuid.UUID,
            body: ProjectUpdate,
            x_user_id: str = Header(..., alias="X-User-Id"),
        ) -> ProjectOut:
            """Partially update a project.  Only fields present in the request
            body are applied — omitted fields retain their current values.

            To clear `github_token`, send `"github_token": null` explicitly.
            """
            # Build kwargs from only the explicitly-set fields so that
            # omitting a field in the JSON body doesn't overwrite it with None.
            updates = body.model_dump(exclude_unset=True)
            project = await self._ctrl.update_project(
                project_id=project_id,
                user_id=x_user_id,
                **updates,
            )
            return ProjectOut.from_model(project)

        @router.delete(
            "/{project_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            response_class=Response,
            summary="Delete project",
        )
        async def delete_project(
            project_id: uuid.UUID,
            x_user_id: str = Header(..., alias="X-User-Id"),
        ) -> Response:
            """Delete a project.  404 if it does not exist or is not owned
            by the authenticated user."""
            await self._ctrl.delete_project(
                project_id=project_id, user_id=x_user_id
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @router.post(
            "/{project_id}/sandbox",
            response_model=ProjectOut,
            summary="Assign sandbox",
        )
        async def assign_sandbox(
            project_id: uuid.UUID,
            body: SandboxAssign,
            x_user_id: str = Header(..., alias="X-User-Id"),
        ) -> ProjectOut:
            """Attach (or replace) the sandbox identifier for a project.

            Called by the sandbox orchestrator once it has allocated a
            sandbox environment for the project.  404 if the project does
            not exist or is not owned by the authenticated user.
            """
            project = await self._ctrl.assign_sandbox(
                project_id=project_id,
                user_id=x_user_id,
                sandbox_id=body.sandbox_id,
            )
            return ProjectOut.from_model(project)
