"""
GeneralAgent — the default multi-purpose agent.

Inherits the LangGraph build that previously lived in `MainAgent`:
two-section flow (qualify → execute) with the demo tools mounted in
the execute phase. The only thing that's actually new at this layer
is the `AgentSpec` declaration — the graph wiring is unchanged.

Once the registry rolls out, `MainAgent` is kept only for back-compat
with code paths that still hold a direct reference. New code should
resolve `AgentRegistry` and call `.get("general")` instead.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, ClassVar

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from .....infrastructure.agentic.agentic_store import AgenticStore
from .....infrastructure.config.env import Env
from ..agent_spec import (
    AgentConstraints,
    AgentSpec,
)
from ..base_agent import BaseAgent

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


@tool
def current_time() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


@tool
def echo(message: str) -> str:
    """Echo a message back to the user verbatim. Useful as a smoke test."""
    return message


_TOOLS = [current_time, echo]


def _build_spec(underlying_model: str) -> AgentSpec:
    """Construct GeneralAgent's spec with the LiteLLM model name plumbed
    in from env. The class-level `spec` is the SAME shape with a
    placeholder model name so `BaseAgent.__init_subclass__` (which
    validates the ClassVar at class-definition time) sees a valid spec;
    the constructor swaps in the env-derived model on each instance.

    Tools are EMPTY for v1. The runtime graph currently has demo tools
    (`current_time`, `echo`) that aren't user-facing; once `web_search`
    and `code_execution` are wired into the graph for real, declare
    them here so the composer renders the corresponding toggles. Until
    then, declaring fake tools would lie to the frontend.
    """
    return AgentSpec(
        id="general",
        display_name="General Assistant",
        description=(
            "Multi-purpose chat. Accepts images and PDFs."
        ),
        icon="sparkles",
        underlying_model=underlying_model,
        accepts_modalities=["text", "image", "pdf"],
        tools=[],
        constraints=AgentConstraints(
            max_runtime_seconds=120,
            streams_partial_tokens=True,
        ),
        visibility="public",
    )


class GeneralAgent(BaseAgent):
    # Placeholder class-level spec satisfies `__init_subclass__` (which
    # checks `cls.__dict__["spec"]` at class-definition time, BEFORE env
    # is loaded). The runtime `self.spec` is rebuilt in `__init__` from
    # `env.litellm_model` so capability declaration tracks deploy config
    # without code changes.
    spec: ClassVar[AgentSpec] = _build_spec("__placeholder__")

    def __init__(self, agentic_store: AgenticStore, env: Env) -> None:
        super().__init__()
        self._agentic = agentic_store
        self._env = env
        # Instance attribute shadows the class-level placeholder. The
        # registry reads `instance.spec`, so the env-derived value wins.
        self.spec = _build_spec(env.litellm_model)

    def _build(self) -> "CompiledStateGraph":
        # Local imports — see the comment in the legacy `MainAgent` for
        # why these are deferred. Same reasoning still applies.
        from react_agent.graph import create_react_agent
        from react_agent.state_machine.section_flow_middleware import (
            SectionFlowMiddleware,
        )
        from react_agent.state_machine.types.config_types import SectionConfig

        # LiteLLM proxy is OpenAI-compatible — `ChatOpenAI` works as-is.
        # Model name comes from the spec, not env, so the capability
        # declaration and the runtime invocation can't drift.
        model = ChatOpenAI(
            model=self.spec.underlying_model,
            api_key=self._env.litellm_proxy_api_key,
            base_url=self._env.litellm_proxy_api_base,
        )

        # Two-section flow: clarify the ask, then act on it.
        sections = {
            "qualify": SectionConfig(
                name="qualify",
                prompt=(
                    "You are in the QUALIFY phase. Ask one short clarifying "
                    "question if the user's request is ambiguous. Otherwise "
                    "call `change_section` with target=`execute` and proceed. "
                    "Do not use other tools in this phase."
                ),
                allowed_transitions=["execute"],
            ),
            "execute": SectionConfig(
                name="execute",
                prompt=(
                    "You are in the EXECUTE phase. Use the available tools "
                    "to fulfil the user's request, then answer concisely."
                ),
                tools=[t.name for t in _TOOLS],
                allowed_transitions=[],
            ),
        }

        section_flow = SectionFlowMiddleware(
            sections=sections,
            initial_section="qualify",
        )

        return create_react_agent(
            model=model,
            tools=_TOOLS,
            system_prompt=(
                "You are Praxis, the platform's main agent. Be precise, "
                "concise, and never invent tool results."
            ),
            middleware=[section_flow],
            checkpointer=self._agentic.checkpointer,
        )
