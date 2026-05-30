"""Sequential Tool Node - runs tools one after another instead of in parallel."""

import asyncio
import logging
from typing import Any, Union

from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolRuntime, get_config_list
from langgraph.runtime import Runtime
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SequentialToolNode(ToolNode):
    """Tool node that enforces sequential execution even across multiple concurrent graph executions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Global lock to prevent ANY parallel tool execution across all instances
        self._execution_lock = asyncio.Lock()

    def _prepare_tool_runtimes(
        self,
        input: Union[list[AnyMessage], dict[str, Any], BaseModel],
        config: RunnableConfig,
        runtime: Runtime,
    ) -> tuple[list, str, list[ToolRuntime]]:
        tool_calls, input_type = self._parse_input(input)
        config_list = get_config_list(config, len(tool_calls))

        tool_runtimes = []
        for call, cfg in zip(tool_calls, config_list, strict=False):
            state = self._extract_state(input)
            tool_runtime = ToolRuntime(
                state=state,
                tool_call_id=call["id"],
                config=cfg,
                context=runtime.context,
                store=runtime.store,
                stream_writer=runtime.stream_writer,
            )
            tool_runtimes.append(tool_runtime)

        return tool_calls, input_type, tool_runtimes

    def _func(
        self,
        input: Union[list[AnyMessage], dict[str, Any], BaseModel],
        config: RunnableConfig,
        runtime: Runtime,
    ) -> Any:
        tool_calls, input_type, tool_runtimes = self._prepare_tool_runtimes(input, config, runtime)

        outputs = []
        for call, tool_runtime in zip(tool_calls, tool_runtimes, strict=False):
            output = self._run_one(call, input_type, tool_runtime)
            outputs.append(output)

        return self._combine_tool_outputs(outputs, input_type)

    async def _afunc(
        self,
        input: Union[list[AnyMessage], dict[str, Any], BaseModel],
        config: RunnableConfig,
        runtime: Runtime,
    ) -> Any:
        # Acquire lock to prevent parallel execution across multiple graph branches/loops
        async with self._execution_lock:
            tool_calls, input_type, tool_runtimes = self._prepare_tool_runtimes(input, config, runtime)

            outputs = []
            for call, tool_runtime in zip(tool_calls, tool_runtimes, strict=False):
                output = await self._arun_one(call, input_type, tool_runtime)
                outputs.append(output)

            return self._combine_tool_outputs(outputs, input_type)


__all__ = ["SequentialToolNode"]
