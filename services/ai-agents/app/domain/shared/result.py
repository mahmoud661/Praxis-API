from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class Result(Generic[T, E]):
    """
    Functional Result type. Use cases return Result instead of raising so
    error paths are visible in the call site signature.
    """

    _ok: bool
    _value: T | None = None
    _error: E | None = None

    @staticmethod
    def ok(value: T) -> "Result[T, E]":
        return Result(_ok=True, _value=value)

    @staticmethod
    def fail(error: E) -> "Result[T, E]":
        return Result(_ok=False, _error=error)

    def is_ok(self) -> bool:
        return self._ok

    def is_fail(self) -> bool:
        return not self._ok

    def value(self) -> T:
        if not self._ok or self._value is None:
            raise RuntimeError("Result.value called on Fail")
        return self._value

    def error(self) -> E:
        if self._ok or self._error is None:
            raise RuntimeError("Result.error called on Ok")
        return self._error
