"""
Agentic LLM orchestration (LangChain / LangGraph).

Contains:
  - `react_agent/` — the handrolled React agent framework (StateGraph builder,
    middleware, state machine, structured output, etc.). Its modules use
    bare absolute imports like `from react_agent.X import Y`, so we add
    THIS folder to `sys.path` below — that makes `react_agent` resolvable
    as a top-level module without rewriting every import site.
  - `main_agent.py` — the one agent the service exposes. Builds a compiled
    LangGraph by wiring the react_agent framework with a state machine.
  - `runner.py` — `AgentRunner` orchestrates a graph run, streams events
    into Redis, hands them to a transport-agnostic `on_event` callback.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `react_agent/*.py` uses `from react_agent.X import Y` (absolute), as if
# react_agent was installed as a top-level package. Adding this folder to
# sys.path makes that work without touching ~27 files of imports inside the
# subpackage. Done at package-load so any import below this point sees it.
_AGENTIC_DIR = str(Path(__file__).resolve().parent)
if _AGENTIC_DIR not in sys.path:
    sys.path.insert(0, _AGENTIC_DIR)
