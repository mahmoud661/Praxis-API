"""
Test doubles. Currently just `SilentLogger` — the agent CRUD stack (entity,
repo, service) was removed because the service has no persistence story
right now. When repos come back, the in-memory fakes return here too.
"""

from __future__ import annotations

from typing import Any


class SilentLogger:
    def debug(self, msg: str, **ctx: Any) -> None: pass
    def info(self, msg: str, **ctx: Any) -> None: pass
    def warning(self, msg: str, **ctx: Any) -> None: pass
    def error(self, msg: str, **ctx: Any) -> None: pass
