"""
MainAgent — back-compat shim over `AgentRegistry`.

Pre-registry, `MainAgent` was the only agent the service exposed and it
built its own LangGraph directly. Now the platform routes every agent
through `AgentRegistry`, but two callers still hold a `MainAgent`
reference (the legacy `AgentRunner` + `TurnsService`). Rather than
diff-touch those, this class proxies their `get()` call to whichever
agent the registry marks as `default_id()`. One graph, one source of
truth, no duplicate tool list.

Resolution is lazy: `__init__` only stashes the registry reference;
`get()` consults `default_id()` + `get(id)` per call (cached on the
underlying agent's own `_graph`). That matches the original
"graph compiles on first call" semantics and avoids ordering issues
between container setup and `registry.discover()` (which runs near
the end of bootstrap).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .agent_registry import AgentRegistry

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class MainAgent:
    """DI-registered as `"MainAgent"`. Thin proxy — owns no graph, no
    tools, no spec. Defers to the registry's default agent."""

    def __init__(self, agent_registry: AgentRegistry) -> None:
        self._registry = agent_registry

    def get(self) -> "CompiledStateGraph":
        """Return the default agent's compiled graph. Callers should
        only invoke this after `AgenticStore.init()` has run AND
        `AgentRegistry.discover()` has completed (both happen during
        boot before any request is served)."""
        agent = self._registry.get(self._registry.default_id())
        return agent.get()
