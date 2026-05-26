from __future__ import annotations

import logging
from typing import Any

import structlog


class StructlogLogger:
    """Adapter implementing the domain Logger port via structlog."""

    def __init__(self, service_name: str, level: int = logging.INFO) -> None:
        logging.basicConfig(format="%(message)s", level=level)
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level),
        )
        self._log = structlog.get_logger().bind(service=service_name)

    def debug(self, msg: str, **ctx: Any) -> None:
        self._log.debug(msg, **ctx)

    def info(self, msg: str, **ctx: Any) -> None:
        self._log.info(msg, **ctx)

    def warning(self, msg: str, **ctx: Any) -> None:
        self._log.warning(msg, **ctx)

    def error(self, msg: str, **ctx: Any) -> None:
        self._log.error(msg, **ctx)
