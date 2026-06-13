"""react_agent — a self-contained React agent library.

Contains the StateGraph builder, the middleware system (including the
attachment preload/compaction + content-reference machinery), the
section state machine, and structured output handling. Designed for
extraction as a standalone package: nothing in here imports the host
application — environmental needs are declared as Protocols in
`react_agent.ports` and `react_agent.references`, and the host passes
implementations in at graph-build time.

The public graph API is exposed lazily (PEP 562): importing the
LIGHTWEIGHT modules (`react_agent.ports`, `react_agent.references`)
must not drag in the LangGraph runtime — hosts import those at module
scope everywhere (error contracts, DTO shims), while the heavy
runtime loads only when an agent actually builds a graph.
"""

from typing import Any

__all__ = ["build_react_agent_graph", "create_react_agent"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        # Package-relative so it resolves within THIS package instance
        # regardless of which addressing loaded it (top-level
        # `react_agent` or the host's package path).
        from . import graph

        return getattr(graph, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
