"""Tunable constants for the agent-side memory tool layer."""

# Number of results requested by memory_search and memory_list tools.
MEMORY_SEARCH_K: int = 10

# Hard upper bound on memory_list result count (enforced in the tool).
MEMORY_LIST_MAX_K: int = 20
