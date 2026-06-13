"""Subagent middleware for LangGraph workflows."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools import BaseTool, ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.prebuilt.interrupt import HumanInterruptConfig as InterruptOnConfig
from langgraph.types import Command

from application.AI.shared.middlewares.human_in_the_loop_middleware import HumanInTheLoopMiddleware
from react_agent.models.agent import EXCLUDED_STATE_KEYS, CompiledSubAgent, SubAgent
from application.AI.workflows.thetester.prompts.subagent_prompts import (
    DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
    DEFAULT_SUBAGENT_PROMPT,
    TASK_SYSTEM_PROMPT,
    TASK_TOOL_DESCRIPTION,
)
from application.DTOs.streaming.event_types import StreamEventType
from application.log import logger
from application.utils.streaming.helpers import emit_after_subagent, emit_before_subagent

__all__ = ["SubAgent", "CompiledSubAgent", "SubAgentMiddleware"]

# Global lock to prevent parallel subagent invocations that share the same DB session
_SUBAGENT_INVOCATION_LOCK = asyncio.Lock()


def _get_subagents(
    *,
    default_model: str | BaseChatModel,
    default_tools: Sequence[BaseTool | Callable | dict[str, Any]],
    default_middleware: list[AgentMiddleware] | None,
    default_interrupt_on: dict[str, bool | InterruptOnConfig] | None,
    subagents: list[SubAgent | CompiledSubAgent],
    general_purpose_agent: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Get compiled subagent runnables and descriptions."""
    from react_agent import create_react_agent as create_agent

    default_subagent_middleware = default_middleware or []

    agents: dict[str, Any] = {}
    subagent_descriptions = []

    if general_purpose_agent:
        general_purpose_middleware = [*default_subagent_middleware]
        if default_interrupt_on:
            general_purpose_middleware.append(HumanInTheLoopMiddleware(interrupt_on=default_interrupt_on))
        general_purpose_subagent = create_agent(
            default_model,
            system_prompt=DEFAULT_SUBAGENT_PROMPT,
            tools=default_tools,
            middleware=general_purpose_middleware,
        )
        agents["general-purpose"] = general_purpose_subagent
        subagent_descriptions.append(f"- general-purpose: {DEFAULT_GENERAL_PURPOSE_DESCRIPTION}")

    for agent_ in subagents:
        subagent_descriptions.append(f"- {agent_['name']}: {agent_['description']}")
        if "runnable" in agent_:
            custom_agent = cast("CompiledSubAgent", agent_)
            agents[custom_agent["name"]] = custom_agent["runnable"]
            continue

        # If a tools_factory is provided, skip static compilation — agent will be built at invocation time
        if "tools_factory" in agent_:
            agents[agent_["name"]] = None  # placeholder; built dynamically
            continue

        _tools = agent_.get("tools", list(default_tools))

        subagent_model = agent_.get("model", default_model)

        _middleware = (
            [*default_subagent_middleware, *agent_["middleware"]]
            if "middleware" in agent_
            else [*default_subagent_middleware]
        )

        interrupt_on = agent_.get("interrupt_on", default_interrupt_on)
        if interrupt_on:
            _middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))

        state_schema = agent_.get("state_schema", None)

        agents[agent_["name"]] = create_agent(
            subagent_model,
            system_prompt=agent_["system_prompt"],
            tools=_tools,
            middleware=_middleware,
            state_schema=state_schema,
        )
    return agents, subagent_descriptions


def _create_task_tool(
    *,
    default_model: str | BaseChatModel,
    default_tools: Sequence[BaseTool | Callable | dict[str, Any]],
    default_middleware: list[AgentMiddleware] | None,
    default_interrupt_on: dict[str, bool | InterruptOnConfig] | None,
    subagents: list[SubAgent | CompiledSubAgent],
    general_purpose_agent: bool,
    task_description: str | None = None,
) -> BaseTool:
    """Create task tool for spawning subagents."""
    subagent_graphs, subagent_descriptions = _get_subagents(
        default_model=default_model,
        default_tools=default_tools,
        default_middleware=default_middleware,
        default_interrupt_on=default_interrupt_on,
        subagents=subagents,
        general_purpose_agent=general_purpose_agent,
    )
    subagent_description_str = "\n".join(subagent_descriptions)

    def _return_command_with_state_update(result: dict, tool_call_id: str, subagent_type: str) -> Command:
        state_update = {k: v for k, v in result.items() if k not in EXCLUDED_STATE_KEYS}

        # Special handling: If knowledge-architect completed, mark kt_built=True and send tree_done event
        if subagent_type == "knowledge-architect":
            state_update["kt_built"] = True

            # Send tree_done stream event
            try:
                writer = get_stream_writer()
                writer(
                    {
                        "type": StreamEventType.KT_BUILT,
                        "action_type": "tree_done",
                        "data_type": "tree_building",
                        "message": "The tree is ready",
                    }
                )
            except Exception:
                pass  # Stream writer not available when not streaming

        # Strip trailing whitespace to prevent API errors with Anthropic
        message_text = result["messages"][-1].text.rstrip() if result["messages"][-1].text else ""

        # For the web-browser subagent, append the locator registry so Ali always
        # receives it regardless of how the agent ended its message.
        if subagent_type == "web-browser":
            from application.AI.workflows.thetester.agents.script_writer.tools.agent_browser.core.context_vars import (  # noqa: PLC0415
                saved_locators_ctx,
            )

            locators = saved_locators_ctx.get()
            if locators:
                lines = ["\n\n--- Saved Locators ---"]
                for loc in locators:
                    tc = loc.get("test_case") or "—"
                    selector = loc.get("selector") or f"@{loc['ref']}"
                    strategy = loc.get("strategy", "?")
                    role = loc.get("role", "")
                    acc_name = loc.get("acc_name", "")
                    page_url = loc.get("page_url", "")
                    find_results = loc.get("find_results", "")
                    html = loc.get("html", "")

                    lines.append(f"\n  '{loc['name']}'")
                    lines.append(f"    selector  : {selector}")
                    lines.append(f"    strategy  : {strategy}  |  tc: {tc}")
                    if role:
                        lines.append(f"    role       : {role}")
                    if acc_name:
                        lines.append(f"    acc_name   : {acc_name}")
                    if page_url:
                        lines.append(f"    page url   : {page_url}")
                    if html:
                        lines.append(f"    html       : {html}")
                    if find_results:
                        lines.append("    find validations:")
                        for result_line in find_results.splitlines():
                            lines.append(f"  {result_line}")
                message_text = message_text + "\n".join(lines)

            # --- Not Found block ---
            from application.AI.workflows.thetester.agents.script_writer.tools.agent_browser.core.context_vars import (  # noqa: PLC0415
                not_found_ctx,
            )

            not_found = not_found_ctx.get()
            if not_found:
                nf_lines = ["\n\n--- Not Found ---"]
                nf_lines.append("These elements were confirmed ABSENT after exhaustive search.")
                nf_lines.append("DO NOT retry them — ask the user to verify the element name or URL.")
                for nf in not_found:
                    tc = nf.get("test_case") or "—"
                    nf_lines.append(f"\n  '{nf['name']}'  (tc: {tc})")
                    nf_lines.append(f"    reason: {nf['reason']}")
                message_text = message_text + "\n".join(nf_lines)

        return Command(
            update={
                **state_update,
                "messages": [ToolMessage(message_text, tool_call_id=tool_call_id)],
            }
        )

    # Build a lookup for subagent configs by name for state preparation
    _subagent_configs: dict[str, dict] = {}
    for _sa in subagents:
        _subagent_configs[_sa["name"]] = _sa

    def _validate_and_prepare_state(
        subagent_type: str, description: str, runtime: ToolRuntime
    ) -> tuple[Runnable, dict]:
        """Prepare state for invocation."""
        subagent = subagent_graphs[subagent_type]
        sa_config = _subagent_configs.get(subagent_type, {})

        # Reset saved locators at the start of every web-browser run so each
        # `task` call gets a clean registry (refs from a previous page are stale).
        if subagent_type == "web-browser":
            from application.AI.workflows.thetester.agents.script_writer.tools.agent_browser.core.context_vars import (  # noqa: PLC0415
                last_snapshot_refs_ctx,
                not_found_ctx,
                saved_locators_ctx,
            )

            saved_locators_ctx.set([])
            last_snapshot_refs_ctx.set({})
            not_found_ctx.set([])

        # Create a new state dict to avoid mutating the original
        subagent_state = {k: v for k, v in runtime.state.items() if k not in EXCLUDED_STATE_KEYS}

        subagent_state["messages"] = [HumanMessage(content=description)]

        # Forward specific state keys if configured
        for key in sa_config.get("state_forwarding_keys", []):
            value = runtime.state.get(key)
            if value is not None:
                subagent_state[key] = value

        # If this subagent uses a tools_factory, build the agent dynamically based on current state
        if subagent is None and "tools_factory" in sa_config:
            from react_agent import create_react_agent as create_agent  # noqa: PLC0415

            _tools = sa_config["tools_factory"](runtime.state)
            _subagent_model = sa_config.get("model", default_model)
            _base_middleware = default_middleware or []
            _subagent_middleware = (
                [*_base_middleware, *sa_config["middleware"]] if "middleware" in sa_config else [*_base_middleware]
            )
            interrupt_on = sa_config.get("interrupt_on", default_interrupt_on)
            if interrupt_on:
                _subagent_middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
            subagent = create_agent(
                _subagent_model,
                system_prompt=sa_config["system_prompt"],
                tools=_tools,
                middleware=_subagent_middleware,
                state_schema=sa_config.get("state_schema", None),
            )

        return subagent, subagent_state

    if task_description is None:
        task_description = TASK_TOOL_DESCRIPTION.format(available_agents=subagent_description_str)
    elif "{available_agents}" in task_description:
        task_description = task_description.format(available_agents=subagent_description_str)

    def task(
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
        config: RunnableConfig,
    ) -> str | Command:
        if subagent_type not in subagent_graphs:
            allowed_types = ", ".join([f"`{k}`" for k in subagent_graphs])
            return f"We cannot invoke subagent {subagent_type} because it does not exist, the only allowed types are {allowed_types}"
        subagent, subagent_state = _validate_and_prepare_state(subagent_type, description, runtime)

        # Prepare execution config with no_stream tag
        my_config = config.copy() if config else {}
        tags = my_config.get("tags", [])
        if "no_stream" not in tags:
            tags.append("no_stream")
        my_config["tags"] = tags

        result = subagent.invoke(subagent_state, my_config)
        if not runtime.tool_call_id:
            value_error_msg = "Tool call ID is required for subagent invocation"
            raise ValueError(value_error_msg)
        return _return_command_with_state_update(result, runtime.tool_call_id, subagent_type)

    async def atask(
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
        config: RunnableConfig,
    ) -> str | Command:
        if subagent_type not in subagent_graphs:
            allowed_types = ", ".join([f"`{k}`" for k in subagent_graphs])
            return f"We cannot invoke subagent {subagent_type} because it does not exist, the only allowed types are {allowed_types}"

        # Use global lock to prevent parallel subagent invocations with shared DB session
        async with _SUBAGENT_INVOCATION_LOCK:
            subagent, subagent_state = _validate_and_prepare_state(subagent_type, description, runtime)

            # Emit progress event when document-processor or confluence-reader starts
            emit_before_subagent(subagent_type)

            # Prepare execution config with no_stream tag
            my_config = config.copy() if config else {}
            tags = my_config.get("tags", [])
            if "no_stream" not in tags:
                tags.append("no_stream")
            my_config["tags"] = tags
            my_config["recursion_limit"] = 1000

            logger.info(f"[Subagent] Invoking {subagent_type} subagent with description: {description[:100]}...")

            result = await subagent.ainvoke(subagent_state, my_config)

            # Emit completion event when document-processor or confluence-reader finishes
            emit_after_subagent(subagent_type)

            if not runtime.tool_call_id:
                value_error_msg = "Tool call ID is required for subagent invocation"
                raise ValueError(value_error_msg)

            return _return_command_with_state_update(result, runtime.tool_call_id, subagent_type)

    return StructuredTool.from_function(
        name="task",
        func=task,
        coroutine=atask,
        description=task_description,
    )


class SubAgentMiddleware(AgentMiddleware):
    """Middleware that provides subagent task tool with optional section-based filtering."""

    def __init__(
        self,
        *,
        default_model: str | BaseChatModel,
        default_tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
        default_middleware: list[AgentMiddleware] | None = None,
        default_interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
        subagents: list[SubAgent | CompiledSubAgent] | None = None,
        system_prompt: str | None = TASK_SYSTEM_PROMPT,
        general_purpose_agent: bool = True,
        task_description: str | None = None,
        section_manager=None,  # Optional: for section-based filtering
    ) -> None:
        """Initialize SubAgentMiddleware.

        Args:
            section_manager: Optional SectionManager for filtering subagents by section
        """
        super().__init__()
        self.system_prompt = system_prompt
        self.section_manager = section_manager
        self._subagents = subagents or []
        self._general_purpose_agent = general_purpose_agent

        task_tool = _create_task_tool(
            default_model=default_model,
            default_tools=default_tools or [],
            default_middleware=default_middleware,
            default_interrupt_on=default_interrupt_on,
            subagents=subagents or [],
            general_purpose_agent=general_purpose_agent,
            task_description=task_description,
        )
        self.tools = [task_tool]

    def _get_filtered_subagent_prompt(self, state: dict) -> str | None:
        """Get filtered subagent descriptions based on current section."""
        if self.section_manager is None:
            return None

        current_section = state.get("current_section")
        if not current_section:
            return None

        section_config = self.section_manager.get_section(current_section)
        if not section_config or section_config.allowed_subagents is None:
            return None

        # Filter subagents
        allowed_names = set(section_config.allowed_subagents)
        filtered_descriptions = []

        if self._general_purpose_agent and "general-purpose" in allowed_names:
            from application.AI.workflows.thetester.prompts.subagent_prompts import DEFAULT_GENERAL_PURPOSE_DESCRIPTION

            filtered_descriptions.append(f"- general-purpose: {DEFAULT_GENERAL_PURPOSE_DESCRIPTION}")

        for subagent in self._subagents:
            if subagent.get("name") in allowed_names:
                filtered_descriptions.append(f"- {subagent['name']}: {subagent['description']}")

        if filtered_descriptions:
            return (
                f"\n\n# Available Subagents (Section: {current_section})\n\n"
                f"You can delegate tasks to these specialized subagents:\n\n"
                f"{chr(10).join(filtered_descriptions)}\n\n"
                f"Use the `task` tool with the appropriate subagent_type."
            )

        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject section-filtered subagents as separate system message."""
        # Add section-filtered subagent info if available
        filtered_prompt = self._get_filtered_subagent_prompt(request.state)
        if filtered_prompt:
            subagent_msg = SystemMessage(content=filtered_prompt)
            modified_messages = [subagent_msg] + request.messages
            return handler(request.override(messages=modified_messages))
        elif self.system_prompt is not None:
            subagent_msg = SystemMessage(content=self.system_prompt)
            modified_messages = [subagent_msg] + request.messages
            return handler(request.override(messages=modified_messages))

        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Inject section-filtered subagents as separate system message (async)."""
        # Add section-filtered subagent info if available
        filtered_prompt = self._get_filtered_subagent_prompt(request.state)
        if filtered_prompt:
            subagent_msg = SystemMessage(content=filtered_prompt)
            modified_messages = [subagent_msg] + request.messages
            return await handler(request.override(messages=modified_messages))
        elif self.system_prompt is not None:
            subagent_msg = SystemMessage(content=self.system_prompt)
            modified_messages = [subagent_msg] + request.messages
            return await handler(request.override(messages=modified_messages))

        return await handler(request)
