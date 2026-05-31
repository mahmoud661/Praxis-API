"""Type aliases for state machine system."""

from __future__ import annotations

from typing import Any, Callable, Literal

# Type aliases for clarity
SectionName = str
PromptPosition = Literal["append", "prepend"]
StateDict = dict[str, Any]
AutoTransitionCondition = Callable[[StateDict], SectionName | None]
StateValidator = Callable[[StateDict], bool]
SectionHook = Callable[[StateDict], dict[str, Any] | None]

__all__ = [
    "SectionName",
    "PromptPosition",
    "StateDict",
    "AutoTransitionCondition",
    "StateValidator",
    "SectionHook",
]
