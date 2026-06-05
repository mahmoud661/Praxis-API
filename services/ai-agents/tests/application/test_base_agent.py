"""Sanity tests for `BaseAgent.__init_subclass__`."""

import pytest

from app.application.services.agentic.agent_spec import AgentSpec
from app.application.services.agentic.base_agent import BaseAgent


def test_subclass_without_spec_rejected_at_class_definition():
    with pytest.raises(TypeError) as exc:
        class NoSpec(BaseAgent):  # type: ignore[misc]
            def _build(self) -> object:
                return None

    assert "must declare `spec" in str(exc.value)


def test_subclass_with_non_spec_value_rejected():
    with pytest.raises(TypeError) as exc:
        class WrongType(BaseAgent):
            spec = "not an AgentSpec"  # type: ignore[assignment]

            def _build(self) -> object:
                return None

    assert "must be an AgentSpec" in str(exc.value)


def test_get_caches_build_result():
    build_calls = []

    class Counting(BaseAgent):
        spec = AgentSpec(
            id="counting",
            display_name="Counting",
            description="Tracks _build invocations.",
            underlying_model="m",
            accepts_modalities=["text"],
        )

        def _build(self) -> object:
            build_calls.append(1)
            return "graph"

    agent = Counting()
    assert agent.get() == "graph"
    assert agent.get() == "graph"
    assert len(build_calls) == 1  # second .get() reused the cache
