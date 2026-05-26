import pytest

from app.domain.shared.exceptions import ValidationException
from app.domain.value_objects.agent_name import AgentName


def test_create_strips_whitespace():
    assert AgentName.create("  hello  ").value == "hello"


@pytest.mark.parametrize("raw", ["", "   ", "x" * 121])
def test_create_rejects_bad_length(raw: str) -> None:
    with pytest.raises(ValidationException):
        AgentName.create(raw)


def test_create_accepts_boundary_lengths():
    assert AgentName.create("a").value == "a"
    assert AgentName.create("x" * 120).value == "x" * 120
