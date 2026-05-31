"""Error handling for structured output validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain.agents.structured_output import ResponseFormat

from react_agent.structured_output.constants import STRUCTURED_OUTPUT_ERROR_TEMPLATE


def handle_structured_output_error(
    exception: Exception,
    response_format: ResponseFormat,
) -> tuple[bool, str]:
    """Handle structured output error. Returns `(should_retry, retry_tool_message)`."""
    from langchain.agents.structured_output import ToolStrategy

    if not isinstance(response_format, ToolStrategy):
        return False, ""

    handle_errors = response_format.handle_errors

    if handle_errors is False:
        return False, ""
    if handle_errors is True:
        return True, STRUCTURED_OUTPUT_ERROR_TEMPLATE.format(error=str(exception))
    if isinstance(handle_errors, str):
        return True, handle_errors
    if isinstance(handle_errors, type) and issubclass(handle_errors, Exception):
        if isinstance(exception, handle_errors):
            return True, STRUCTURED_OUTPUT_ERROR_TEMPLATE.format(error=str(exception))
        return False, ""
    if isinstance(handle_errors, tuple):
        if any(isinstance(exception, exc_type) for exc_type in handle_errors):
            return True, STRUCTURED_OUTPUT_ERROR_TEMPLATE.format(error=str(exception))
        return False, ""
    if callable(handle_errors):
        # type narrowing not working appropriately w/ callable check, can fix later
        return True, handle_errors(exception)  # type: ignore[return-value,call-arg]
    return False, ""
