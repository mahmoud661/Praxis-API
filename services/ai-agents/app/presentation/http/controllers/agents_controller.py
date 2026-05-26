from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ....application.dtos import CreateAgentInput
from ....application.use_cases.create_agent import CreateAgentUseCase
from ....application.use_cases.list_user_agents import ListUserAgentsUseCase
from ..dependencies import current_user_id
from ..result_mapper import raise_for_error


class CreateAgentBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_prompt: str = Field(default="", max_length=8000)


class AgentResponse(BaseModel):
    id: str
    name: str
    system_prompt: str
    created_at: str


def make_router(*, create: CreateAgentUseCase, list_for_user: ListUserAgentsUseCase) -> APIRouter:
    """Router factory — accepts use cases as parameters (constructor injection
    of the controller). Composition root wires the concrete use cases."""
    router = APIRouter(prefix="/agents", tags=["agents"])

    @router.post("", response_model=AgentResponse, status_code=201)
    async def create_agent(
        body: CreateAgentBody,
        user_id: str = Depends(current_user_id),
    ) -> AgentResponse:
        result = await create.execute(
            CreateAgentInput(
                owner_id=user_id,
                name=body.name,
                system_prompt=body.system_prompt,
            )
        )
        if result.is_fail():
            raise_for_error(result.error())
        v = result.value()
        return AgentResponse(
            id=v.id,
            name=v.name,
            system_prompt=v.system_prompt,
            created_at=v.created_at,
        )

    @router.get("", response_model=list[AgentResponse])
    async def list_agents(user_id: str = Depends(current_user_id)) -> list[AgentResponse]:
        result = await list_for_user.execute(user_id)
        if result.is_fail():
            raise_for_error(result.error())
        return [
            AgentResponse(
                id=a.id,
                name=a.name,
                system_prompt=a.system_prompt,
                created_at=a.created_at,
            )
            for a in result.value()
        ]

    return router
