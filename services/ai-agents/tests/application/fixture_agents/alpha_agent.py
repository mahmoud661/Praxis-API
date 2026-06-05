"""Test fixture: a minimal BaseAgent the registry tests can discover.

`_build()` returns a sentinel rather than a real compiled graph — the
registry tests never call it, but the abstract method has to be
implemented for the class to instantiate."""

from typing import ClassVar

from app.application.services.agentic.agent_spec import AgentSpec
from app.application.services.agentic.base_agent import BaseAgent


class AlphaAgent(BaseAgent):
    spec: ClassVar[AgentSpec] = AgentSpec(
        id="alpha",
        display_name="Alpha",
        description="Fixture agent for registry tests.",
        underlying_model="test-model",
        accepts_modalities=["text", "image"],
    )

    def _build(self) -> object:
        return "alpha-graph"
