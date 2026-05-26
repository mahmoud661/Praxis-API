import re

import pytest

from app.domain.value_objects.identifiers import AgentId, OwnerId


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def test_agent_id_generate_is_uuid():
    assert UUID_RE.match(AgentId.generate().value)


def test_agent_id_from_str_validates():
    AgentId.from_str("11111111-2222-4333-8444-555555555555")
    with pytest.raises(ValueError):
        AgentId.from_str("nope")


def test_owner_id_is_separate_type_from_agent_id():
    """Both are UUID-validated but the types are distinct, so the type checker
    catches accidentally passing an AgentId where an OwnerId is expected."""
    aid = AgentId.generate()
    oid = OwnerId.from_str("11111111-2222-4333-8444-555555555555")
    # Both are dataclasses with `value`; identity-equality cares about the class.
    assert aid != oid
    assert type(aid).__name__ != type(oid).__name__
