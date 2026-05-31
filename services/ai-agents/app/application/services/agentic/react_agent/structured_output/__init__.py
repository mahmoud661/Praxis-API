"""Structured output handling utilities."""

from __future__ import annotations

from react_agent.structured_output.constants import (
    FALLBACK_MODELS_WITH_STRUCTURED_OUTPUT,
    STRUCTURED_OUTPUT_ERROR_TEMPLATE,
)
from react_agent.structured_output.error_handling import handle_structured_output_error
from react_agent.structured_output.strategy_detection import supports_provider_strategy

__all__ = [
    "FALLBACK_MODELS_WITH_STRUCTURED_OUTPUT",
    "STRUCTURED_OUTPUT_ERROR_TEMPLATE",
    "handle_structured_output_error",
    "supports_provider_strategy",
]
