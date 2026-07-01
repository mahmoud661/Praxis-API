"""HTTP adapter for the projects service — implements IProjectsClient.

Thin httpx wrapper. Multi-tenancy is enforced by forwarding `owner_id`
via the `X-User-Id` header (the same convention the gateway uses for
internal hops). Read-only: the agent never mutates projects.

The projects service mounts its routes under `/projects`, so a single
project is `GET {base}/projects/{id}`.
"""
from __future__ import annotations

import httpx

from ...domain.ports.i_projects_client import IProjectsClient, ProjectContext  # noqa: F401
from ...infrastructure.config.env import Env


class HttpProjectsClient:
    """Implements IProjectsClient against the projects-service REST API.

    DI token: ``"IProjectsClient"`` (resolved by annotation class name —
    the annotation is `IProjectsClient`, the registered value is this
    instance).
    """

    def __init__(self, env: Env) -> None:
        self._http = httpx.AsyncClient(
            base_url=env.projects_service_url.rstrip("/"),
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        )

    async def get_project(
        self, *, project_id: str, owner_id: str
    ) -> ProjectContext | None:
        try:
            r = await self._http.get(
                f"/projects/{project_id}",
                headers={"x-user-id": owner_id},
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
        except Exception:  # noqa: BLE001
            # Best-effort: a slow/down projects service must never fail a
            # run. The caller falls back to no project context.
            return None

        repo = data.get("github_repo_url")
        sandbox = data.get("sandbox_id")
        return ProjectContext(
            id=str(data.get("id", project_id)),
            name=str(data.get("name", "")),
            github_repo_url=repo if isinstance(repo, str) else None,
            sandbox_id=sandbox if isinstance(sandbox, str) else None,
        )
