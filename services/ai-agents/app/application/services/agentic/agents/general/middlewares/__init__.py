"""Middlewares specific to the general agent.

Empty on purpose: everything the general agent runs — attachment
preload/compaction, history compaction, content references, prompt
caching, section flow — ships with the react_agent library. An
agent-SPECIFIC middleware (one that encodes this agent's business
rules rather than reusable runtime behavior) would live here and be
mounted by `graph.py`."""
