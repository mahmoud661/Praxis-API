"""
BaseAgent — abstract base for every agent the platform exposes.

An agent is a (spec, compiled-LangGraph-graph) pair. The spec is the
user-facing capability declaration (`AgentSpec`); the graph is the
LangGraph state machine the runner executes against.

Subclassing contract:

  class GeneralAgent(BaseAgent):
      spec: ClassVar[AgentSpec] = AgentSpec(...)

      def _build(self) -> CompiledStateGraph:
          # Compose middleware + tools + checkpointer.
          ...

Graph compilation is lazy: subclasses implement `_build()`, and the base
class caches the result on first `get()`. This matches `MainAgent`'s
existing pattern — graphs depend on `AgenticStore.init()` having run,
which only happens during the FastAPI lifespan.

The agent registry discovers `BaseAgent` subclasses by scanning the
`agents/` folder; it does NOT touch `_build()` (only reads `spec`).
That keeps the discovery cheap — boot doesn't pay the cost of compiling
every graph just to enumerate the catalog.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from .agent_spec import AgentSpec

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class BaseAgent(ABC):
    # Subclasses MUST override. Class-level so the registry can read it
    # without instantiating the agent (graph compilation is expensive).
    spec: ClassVar[AgentSpec]

    def __init__(self) -> None:
        self._graph: object | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        # Catch missing `spec` at class-definition time rather than at
        # first `get()`. ABCs don't enforce ClassVars natively.
        super().__init_subclass__(**kwargs)
        if "spec" not in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} must declare `spec: ClassVar[AgentSpec] = ...`"
            )
        if not isinstance(cls.__dict__["spec"], AgentSpec):
            raise TypeError(
                f"{cls.__name__}.spec must be an AgentSpec, "
                f"got {type(cls.__dict__['spec']).__name__}"
            )

    def get(self) -> "CompiledStateGraph":
        """Return the compiled graph, building on first call. Callers
        should only invoke this AFTER `AgenticStore.init()` has run —
        same constraint MainAgent had."""
        if self._graph is None:
            self._graph = self._build()
        return self._graph  # type: ignore[return-value]

    @abstractmethod
    def _build(self) -> "CompiledStateGraph":
        """Compile and return the LangGraph state graph. Called once,
        the result is cached on `self`."""
        raise NotImplementedError
