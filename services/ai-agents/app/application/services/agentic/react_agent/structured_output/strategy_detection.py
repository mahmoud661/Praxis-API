"""Model capability detection for structured output strategies."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

from react_agent.structured_output.constants import FALLBACK_MODELS_WITH_STRUCTURED_OUTPUT


def supports_provider_strategy(model: str | BaseChatModel, tools: list | None = None) -> bool:
    """Check if a model supports provider-specific structured output.

    Args:
        model: Model name string or `BaseChatModel` instance.
        tools: Optional list of tools provided to the agent. Needed because some models
            don't support structured output together with tool calling.

    Returns:
        `True` if the model supports provider-specific structured output, `False` otherwise.
    """
    from langchain_core.language_models.chat_models import BaseChatModel

    model_name: str | None = None
    if isinstance(model, str):
        model_name = model
    elif isinstance(model, BaseChatModel):
        model_name = (
            getattr(model, "model_name", None) or getattr(model, "model", None) or getattr(model, "model_id", "")
        )
        model_profile = model.profile
        if (
            model_profile is not None
            and model_profile.get("structured_output")
            # We make an exception for Gemini models, which currently do not support
            # simultaneous tool use with structured output
            and not (tools and isinstance(model_name, str) and "gemini" in model_name.lower())
        ):
            return True

    return any(part in model_name.lower() for part in FALLBACK_MODELS_WITH_STRUCTURED_OUTPUT) if model_name else False
