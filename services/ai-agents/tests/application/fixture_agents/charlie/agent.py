"""Test fixture: minimal package-layout agent — the registry must
discover `charlie/agent.py` by the folder convention."""

from typing import ClassVar

from app.application.services.agentic.agent_spec import AgentSpec
from app.application.services.agentic.base_agent import BaseAgent


class CharlieAgent(BaseAgent):
    spec: ClassVar[AgentSpec] = AgentSpec(
        id="charlie",
        display_name="Charlie",
        description="Package-layout fixture agent for registry tests.",
        underlying_model="test-model",
        accepts_modalities=["text"],
    )

    def _build(self) -> object:
        return "charlie-graph"
