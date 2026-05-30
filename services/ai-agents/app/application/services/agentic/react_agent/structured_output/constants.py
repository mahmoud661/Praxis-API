"""Constants for structured output handling."""

from __future__ import annotations

STRUCTURED_OUTPUT_ERROR_TEMPLATE = "Error: {error}\n Please fix your mistakes."

FALLBACK_MODELS_WITH_STRUCTURED_OUTPUT = [
    # if model profile data are not available, these models are assumed to support
    # structured output
    "grok",
    "gpt-5",
    "gpt-4.1",
    "gpt-4o",
    "gpt-oss",
    "o3-pro",
    "o3-mini",
]
