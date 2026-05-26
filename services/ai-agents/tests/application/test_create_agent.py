import pytest

from app.application.dtos import CreateAgentInput
from app.application.use_cases.create_agent import CreateAgentUseCase
from app.domain.shared.exceptions import DomainException, ValidationException
from tests.helpers.fakes import InMemoryAgentRepository, SilentLogger


OWNER = "11111111-2222-4333-8444-555555555555"


@pytest.mark.asyncio
async def test_create_persists_and_returns_view():
    repo = InMemoryAgentRepository()
    uc = CreateAgentUseCase(repo, SilentLogger())

    result = await uc.execute(
        CreateAgentInput(owner_id=OWNER, name="Helper", system_prompt="be brief"),
    )

    assert result.is_ok()
    view = result.value()
    assert view.owner_id == OWNER
    assert view.name == "Helper"
    assert view.system_prompt == "be brief"
    assert len(repo.all) == 1


@pytest.mark.asyncio
async def test_create_rejects_blank_name_with_domain_error():
    repo = InMemoryAgentRepository()
    uc = CreateAgentUseCase(repo, SilentLogger())

    result = await uc.execute(CreateAgentInput(owner_id=OWNER, name=""))

    assert result.is_fail()
    err = result.error()
    assert isinstance(err, ValidationException)
    assert isinstance(err, DomainException)
    assert repo.all == []


@pytest.mark.asyncio
async def test_create_rejects_invalid_owner_id():
    repo = InMemoryAgentRepository()
    uc = CreateAgentUseCase(repo, SilentLogger())

    # Invalid UUID raises in OwnerId.from_str → ValueError; not a DomainException,
    # so it should bubble up rather than be swallowed by the use case's catch.
    with pytest.raises(ValueError):
        await uc.execute(CreateAgentInput(owner_id="not-a-uuid", name="x"))
