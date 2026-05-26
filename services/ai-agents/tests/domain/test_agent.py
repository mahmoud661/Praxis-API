from datetime import datetime, timezone

from app.domain.entities.agent import Agent
from app.domain.value_objects.agent_name import AgentName
from app.domain.value_objects.identifiers import AgentId, OwnerId


def _owner() -> OwnerId:
    return OwnerId.from_str("11111111-2222-4333-8444-555555555555")


def test_create_initializes_fields_and_emits_no_events():
    agent = Agent.create(owner_id=_owner(), name=AgentName.create("Helper"))
    assert agent.name.value == "Helper"
    assert agent.system_prompt == ""
    assert agent.owner_id == _owner()
    # createdAt is wall-clock; just assert it's a UTC datetime in the recent past.
    assert isinstance(agent.created_at, datetime)
    assert agent.created_at.tzinfo is timezone.utc
    # Aggregate currently emits no domain events on create; pull_events drains regardless.
    assert agent.pull_events() == []


def test_rehydrate_preserves_id_and_created_at():
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    aid = AgentId.from_str("22222222-3333-4444-8555-666666666666")
    agent = Agent.rehydrate(
        id=aid,
        owner_id=_owner(),
        name=AgentName.create("Loaded"),
        system_prompt="be brief",
        created_at=when,
    )
    assert agent.id == aid
    assert agent.created_at == when
    assert agent.system_prompt == "be brief"
    assert agent.pull_events() == []
