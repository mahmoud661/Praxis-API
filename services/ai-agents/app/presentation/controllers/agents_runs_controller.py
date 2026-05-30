"""
REST controller for run-state queries. Lives next to the WS routes; the
WebSockets push real-time updates, this endpoint is what the frontend
calls once at boot to hydrate the sidebar.
"""

from __future__ import annotations

from fastapi import Depends
from pydantic import BaseModel

from ...application.services.agentic.run_manager import RunManager
from ..http.dependencies import current_user_id


class ActiveRunsResponse(BaseModel):
    thread_ids: list[str]


class AgentsRunsController:
    def __init__(self, run_manager: RunManager) -> None:
        # Container resolves `run_manager: RunManager` from "RunManager".
        self._runs = run_manager

    async def active(
        self, user_id: str = Depends(current_user_id)
    ) -> ActiveRunsResponse:
        """List the thread_ids currently running for the caller. Read from
        the Redis set the RunManager maintains."""
        thread_ids = await self._runs.active_runs(user_id)
        return ActiveRunsResponse(thread_ids=thread_ids)
