"""Model output handling logic (domain layer)."""

from typing import Any

from langchain.agents.structured_output import (
    MultipleStructuredOutputsError,
    OutputToolBinding,
    ProviderStrategy,
    ProviderStrategyBinding,
    ResponseFormat,
    StructuredOutputError,
    StructuredOutputValidationError,
    ToolStrategy,
)
from langchain_core.messages import AIMessage, ToolMessage


def handle_model_output(
    output: AIMessage,
    effective_response_format: ResponseFormat | None,
    structured_output_tools: dict[str, OutputToolBinding],
) -> dict[str, Any]:
    """Handle model output and extract structured responses.

    Args:
        output: The AI message output from the model.
        effective_response_format: The response format strategy being used.
        structured_output_tools: Dictionary of structured output tool bindings.

    Returns:
        Dictionary containing "messages" and optionally "structured_response".

    Raises:
        StructuredOutputValidationError: If structured output parsing fails.
        MultipleStructuredOutputsError: If multiple structured outputs detected.
    """
    # Import here to avoid circular dependency
    from react_agent.structured_output import handle_structured_output_error

    # Handle structured output with provider strategy
    if isinstance(effective_response_format, ProviderStrategy):
        if not output.tool_calls:
            provider_strategy_binding = ProviderStrategyBinding.from_schema_spec(effective_response_format.schema_spec)
            try:
                structured_response = provider_strategy_binding.parse(output)
            except Exception as exc:
                schema_name = getattr(
                    effective_response_format.schema_spec.schema,
                    "__name__",
                    "response_format",
                )
                validation_error = StructuredOutputValidationError(schema_name, exc, output)
                raise validation_error from exc
            else:
                return {
                    "messages": [output],
                    "structured_response": structured_response,
                }
        return {"messages": [output]}

    # Handle structured output with tool strategy
    if isinstance(effective_response_format, ToolStrategy) and isinstance(output, AIMessage) and output.tool_calls:
        structured_tool_calls = [tc for tc in output.tool_calls if tc["name"] in structured_output_tools]

        if structured_tool_calls:
            exception: StructuredOutputError | None = None
            if len(structured_tool_calls) > 1:
                # Handle multiple structured outputs error
                tool_names = [tc["name"] for tc in structured_tool_calls]
                exception = MultipleStructuredOutputsError(tool_names, output)
                should_retry, error_message = handle_structured_output_error(exception, effective_response_format)
                if not should_retry:
                    raise exception

                # Add error messages and retry
                tool_messages = [
                    ToolMessage(
                        content=error_message,
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                    for tc in structured_tool_calls
                ]
                return {"messages": [output, *tool_messages]}

            # Handle single structured output
            tool_call = structured_tool_calls[0]
            try:
                structured_tool_binding = structured_output_tools[tool_call["name"]]
                structured_response = structured_tool_binding.parse(tool_call["args"])

                tool_message_content = (
                    effective_response_format.tool_message_content
                    if effective_response_format.tool_message_content
                    else f"Returning structured response: {structured_response}"
                )

                return {
                    "messages": [
                        output,
                        ToolMessage(
                            content=tool_message_content,
                            tool_call_id=tool_call["id"],
                            name=tool_call["name"],
                        ),
                    ],
                    "structured_response": structured_response,
                }
            except Exception as exc:
                exception = StructuredOutputValidationError(tool_call["name"], exc, output)
                should_retry, error_message = handle_structured_output_error(exception, effective_response_format)
                if not should_retry:
                    raise exception from exc

                return {
                    "messages": [
                        output,
                        ToolMessage(
                            content=error_message,
                            tool_call_id=tool_call["id"],
                            name=tool_call["name"],
                        ),
                    ],
                }

    return {"messages": [output]}
