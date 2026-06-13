"""Second fixture agent — proves the registry handles multi-agent
discovery and that `default_id()` picks deterministically."""

from typing import ClassVar

from app.application.services.agentic.agent_spec import AgentSpec
from app.application.services.agentic.base_agent import BaseAgent


class BravoAgent(BaseAgent):
    spec: ClassVar[AgentSpec] = AgentSpec(
        id="bravo",
        display_name="Bravo",
        description="Another fixture agent.",
        underlying_model="test-model",
        accepts_modalities=["text"],
    )

    def _build(self) -> object:
        return "bravo-graph"
