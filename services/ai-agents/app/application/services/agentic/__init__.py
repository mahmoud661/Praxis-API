"""
Agentic LLM orchestration (LangChain / LangGraph).

Layout + flow:

    api → controller → service → runner → agents/<name> → react_agent

  - `react_agent/` — the React agent LIBRARY (StateGraph builder,
    middleware incl. the attachment system, state machine, structured
    output). Destined for extraction as a standalone package: it holds
    NO app imports and NO storage — everything environmental enters
    through the Protocols in `react_agent/ports.py` and
    `react_agent/references.py`. Its core modules use bare absolute
    imports (`from react_agent.X import Y`), so we add THIS folder to
    `sys.path` below.
  - `agents/` — one folder per agent (`agents/general/`), each with its
    own `agent.py` (DI seam + spec), `graph.py` (assembly), `prompts/`,
    `sections.py`, `tools/`, `middlewares/`. Agents are the ONLY thing
    that imports the react_agent runtime.
  - `runner.py` — `AgentRunner` resolves an agent via the registry and
    streams its graph's events to a transport-agnostic `on_event`
    callback. It never touches react_agent directly.
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
