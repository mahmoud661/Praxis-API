import pytest

from app.application.use_cases.provision_default_agent import (
    ProvisionDefaultAgentUseCase,
)
from tests.helpers.fakes import InMemoryAgentRepository, SilentLogger


OWNER = "11111111-2222-4333-8444-555555555555"


def envelope(payload: dict) -> dict:
    """Mirror the Kafka envelope shape produced by the auth service."""
    return {
        "metadata": {
            "eventId": "e-1",
            "occurredAt": "2024-01-01T00:00:00Z",
            "eventName": "UserRegistered",
            "aggregateId": payload.get("userId", ""),
            "version": 1,
        },
        "payload": payload,
    }


@pytest.mark.asyncio
async def test_provisions_a_default_agent_for_new_user():
    repo = InMemoryAgentRepository()
    uc = ProvisionDefaultAgentUseCase(repo, SilentLogger())

    await uc.execute(envelope({"userId": OWNER, "email": "a@b.co"}))

    assert len(repo.all) == 1
    agent = repo.all[0]
    assert agent.owner_id.value == OWNER
    assert agent.name.value == "Default Agent"


@pytest.mark.asyncio
async def test_ignores_envelopes_without_user_id():
    repo = InMemoryAgentRepository()
    uc = ProvisionDefaultAgentUseCase(repo, SilentLogger())

    await uc.execute(envelope({}))  # no userId

    assert repo.all == []
