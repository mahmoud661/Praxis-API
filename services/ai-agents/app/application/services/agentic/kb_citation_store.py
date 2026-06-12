"""
Shared key layout for kb_search citation hits in the agentic k/v store.

Two parties read/write the same keys and must agree on the layout:

  - the `kb_search` tool (agents/general/tools) WRITES each call's
    structured hits + maintains the per-thread call counter, and mints
    `citeturn{seq}search{n}` aliases from the same sequence number;
  - `ContentReferenceLookupService` READS a call's hits back when the
    content-reference system resolves a citation alias.

Keeping the namespace + key functions here (instead of inside the
tool) means the lookup service never has to import from a specific
agent's private tools folder.
"""

from __future__ import annotations

# Namespace in the LangGraph k/v store where each kb_search call
# persists its structured hits, keyed by a per-thread SEARCH SEQUENCE
# number (0-based, monotonic within the thread). The model-facing
# alias is `citeturn{seq}search{n}` — the `turn{seq}` segment
# identifies WHICH kb_search call produced the hit, so two calls in
# one assistant turn can't collide.
KB_HITS_NAMESPACE = ("kb_search_hits",)


def kb_hits_key(thread_id: str, seq: int) -> str:
    """Store key for one kb_search call's hits."""
    return f"{thread_id}:seq:{seq}"


def kb_count_key(thread_id: str) -> str:
    """Store key holding the per-thread count of hit-returning
    kb_search calls so far (the next call's sequence number)."""
    return f"{thread_id}:__count__"
