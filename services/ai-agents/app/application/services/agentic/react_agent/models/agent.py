"""Subagent type definitions."""

from collections.abc import Callable, Sequence
from typing import Any, NotRequired

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from typing_extensions import TypedDict


class SubAgent(TypedDict):
    """SubAgent specification for creating task-specific agents."""

    name: str
    """The name of the agent."""

    description: str
    """The description of the agent."""

    system_prompt: str
    """The system prompt to use for the agent."""

    tools: Sequence[BaseTool | Callable | dict[str, Any]]
    """The tools to use for the agent."""

    model: NotRequired[str | BaseChatModel]
    """The model for the agent. Defaults to `default_model`."""

    middleware: NotRequired[list[Any]]  # AgentMiddleware type would create circular import
    """Additional middleware to append after `default_middleware`."""

    interrupt_on: NotRequired[dict[str, bool | Any]]  # InterruptOnConfig would create circular import
    """The tool configs to use for the agent."""

    state_schema: NotRequired[type]
    """Optional state schema for the subagent. When set, the agent is created with this schema."""

    state_forwarding_keys: NotRequired[list[str]]
    """Extra state keys to explicitly forward from parent state to the subagent (e.g. 'confluence_url')."""


class CompiledSubAgent(TypedDict):
    """A pre-compiled agent spec."""

    name: str
    """The name of the agent."""

    description: str
    """The description of the agent."""

    runnable: Runnable
    """The Runnable to use for the agent."""


# State keys that are excluded when passing state to subagents and when returning
# updates from subagents.
# When returning updates:
# 1. The messages key is handled explicitly to ensure only the final message is included
# 2. The todos and structured_response keys are excluded as they do not have a defined reducer
#    and no clear meaning for returning them from a subagent to the main agent.
EXCLUDED_STATE_KEYS = {"messages", "todos", "structured_response", "summary", "last_covered_index"}
