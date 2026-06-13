from __future__ import annotations

from typing import Any, Protocol


class Logger(Protocol):
    def debug(self, msg: str, **ctx: Any) -> None: pass
    def info(self, msg: str, **ctx: Any) -> None: pass
    def warning(self, msg: str, **ctx: Any) -> None: pass
    def error(self, msg: str, **ctx: Any) -> None: pass
