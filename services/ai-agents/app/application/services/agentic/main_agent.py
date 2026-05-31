"""
MainAgent — the one agent the service exposes.

Build steps:
  1. Resolve the model via `langchain.init_chat_model` (configurable via
     `AGENT_MODEL` env var, e.g. `openai:gpt-4o-mini` or
     `anthropic:claude-3-5-sonnet-latest`).
  2. Define a small set of demonstration tools.
  3. Wire `SectionFlowMiddleware` (the custom state machine in
     `react_agent/state_machine/`) so the agent has two phases:
       - `qualify`: clarify what the user wants, no tools.
       - `execute`: use tools to deliver. Transition to here once we have
         a clear ask.
  4. Compile through the custom `react_agent.create_react_agent` builder,
     passing the LangGraph `AsyncPostgresSaver` checkpointer so per-thread
     state persists across requests/reconnects.

The compiled graph is built lazily on first `get()` because the checkpointer
isn't connected until the FastAPI lifespan runs `AgenticStore.init()`.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# Importing this module triggers `agentic/__init__.py` automatically (Python
# loads parent packages before submodules), which adds this folder to
# sys.path — so the `from react_agent.X` absolute imports inside the
# subpackage resolve from `_build()` below without any further setup.

# Imported lazily inside `_build()` so importing this module doesn't pull in
# the entire react_agent tree (which has heavy LangChain side-effects) until
# the agent is actually built.
if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

from ....infrastructure.agentic.agentic_store import AgenticStore
from ....infrastructure.config.env import Env


# ---------------------------------------------------------------------------
# Tools — kept small and side-effect-free so the demo agent works without
# external API keys beyond the model provider's.
# ---------------------------------------------------------------------------


@tool
def current_time() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


@tool
def echo(message: str) -> str:
    """Echo a message back to the user verbatim. Useful as a smoke test."""
    return message


_TOOLS = [current_time, echo]


# ---------------------------------------------------------------------------
# MainAgent — DI-registered as "MainAgent". Lazy graph compilation.
# ---------------------------------------------------------------------------


class MainAgent:
    def __init__(self, agentic_store: AgenticStore, env: Env) -> None:
        self._agentic = agentic_store
        self._env = env
        self._graph: object | None = None  # CompiledStateGraph; typed as object to avoid heavy import

    def get(self) -> "CompiledStateGraph":
        """Return the compiled graph, building it on first call. Callers
        should only invoke this *after* `AgenticStore.init()` has run."""
        if self._graph is None:
            self._graph = self._build()
        return self._graph  # type: ignore[return-value]

    def _build(self):
        # Local imports — see the module docstring for why these are deferred.
        from react_agent.graph import create_react_agent
        from react_agent.state_machine.section_flow_middleware import (
            SectionFlowMiddleware,
        )
        from react_agent.state_machine.types.config_types import SectionConfig

        # LiteLLM proxy is OpenAI-compatible — `ChatOpenAI` works as-is once
        # we point it at the proxy's base URL with the proxy API key. The
        # model name is whatever the proxy routes upstream (e.g. gpt-5.4-mini).
        model = ChatOpenAI(
            model=self._env.litellm_model,
            api_key=self._env.litellm_proxy_api_key,
            base_url=self._env.litellm_proxy_api_base,
        )

        # Two-section flow: clarify the ask, then act on it. The auto-transition
        # check is intentionally simple — the model decides via the
        # `change_section` tool that SectionFlowMiddleware adds for it.
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
