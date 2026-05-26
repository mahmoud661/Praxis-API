import pytest

from app.application.dtos import CreateAgentInput
from app.application.use_cases.create_agent import CreateAgentUseCase
from app.application.use_cases.list_user_agents import ListUserAgentsUseCase
from tests.helpers.fakes import InMemoryAgentRepository, SilentLogger


OWNER_A = "11111111-2222-4333-8444-555555555555"
OWNER_B = "22222222-3333-4444-8555-666666666666"


@pytest.mark.asyncio
async def test_lists_only_agents_owned_by_caller():
    repo = InMemoryAgentRepository()
    create = CreateAgentUseCase(repo, SilentLogger())
    await create.execute(CreateAgentInput(owner_id=OWNER_A, name="A1"))
    await create.execute(CreateAgentInput(owner_id=OWNER_A, name="A2"))
    await create.execute(CreateAgentInput(owner_id=OWNER_B, name="B1"))

    listed = await ListUserAgentsUseCase(repo).execute(OWNER_A)

    assert listed.is_ok()
    names = sorted(v.name for v in listed.value())
    assert names == ["A1", "A2"]


@pytest.mark.asyncio
async def test_returns_empty_list_for_owner_with_no_agents():
    repo = InMemoryAgentRepository()
    listed = await ListUserAgentsUseCase(repo).execute(OWNER_A)
    assert listed.is_ok()
    assert listed.value() == []
