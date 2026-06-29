"""Tunable domain constants for the memory pipeline.

These govern business-logic rules — distinct from infrastructure config
(connection strings, API keys) which live in env.py.
"""

# Maximum characters per episode body sent to Graphiti for LLM extraction.
# Keeps extraction prompts within reliable token limits (~1 000 tokens).
MAX_EPISODE_CHARS: int = 4_000

# Minimum Graphiti score (0–1) for a new fact episode to be treated as a
# near-duplicate of an existing one and skip re-extraction.
# Only applied to source=="fact"; conversational episodes are always stored
# because they are temporally distinct even when content is similar.
SEMANTIC_DEDUP_THRESHOLD: float = 0.92

# Minimum Graphiti score for forget() to include a hit in the deletion set.
# Prevents loosely-related episodes being wiped when the user says "forget X".
FORGET_SCORE_THRESHOLD: float = 0.6

# Seconds a get_context_summary() result is served from in-process cache
# before a fresh Neo4j query is issued. Invalidated eagerly on every episode
# write so a user who stores a memory sees it reflected on the next turn.
CONTEXT_CACHE_TTL: float = 300.0

# Default result counts for search and list operations.
DEFAULT_SEARCH_K: int = 10
DEFAULT_LIST_K: int = 20
