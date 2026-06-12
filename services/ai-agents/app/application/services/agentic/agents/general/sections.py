"""State-machine sections for the general agent.

Two phases: QUALIFY (clarify ambiguous requests, no tools except the
transition) → EXECUTE (full tool palette, answer). The transition is
agent-initiated via the `change_section` tool that
`SectionFlowMiddleware` injects.

`build_sections` takes the EXECUTE-phase tool names as input so the
section definition never hard-codes what `graph.py` decided to mount —
add a tool to the agent and the section picks it up automatically.
"""

from __future__ import annotations

# Top-level (`react_agent.…`) addressing on purpose — it must be the
# SAME class instance `SectionFlowMiddleware`'s manager validates.
# This module pulls the react_agent runtime at import time, which is
# fine because it's only ever imported by the lazily-loaded graph.py.
from react_agent.state_machine.types.config_types import SectionConfig

from .prompts import EXECUTE_PROMPT, QUALIFY_PROMPT

INITIAL_SECTION = "qualify"


def build_sections(*, execute_tools: list[str]) -> dict[str, SectionConfig]:
    """SectionConfig map for `SectionFlowMiddleware`."""

    return {
        "qualify": SectionConfig(
            name="qualify",
            prompt=QUALIFY_PROMPT,
            allowed_transitions=["execute"],
        ),
        "execute": SectionConfig(
            name="execute",
            prompt=EXECUTE_PROMPT,
            tools=execute_tools,
            allowed_transitions=[],
        ),
    }
