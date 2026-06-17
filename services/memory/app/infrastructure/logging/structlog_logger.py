from __future__ import annotations

import structlog


class StructlogLogger:
    def __init__(self, service_name: str) -> None:
        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ]
        )
        self._log = structlog.get_logger().bind(service=service_name)

    def debug(self, msg: str, **ctx) -> None:
        self._log.debug(msg, **ctx)

    def info(self, msg: str, **ctx) -> None:
        self._log.info(msg, **ctx)

    def warning(self, msg: str, **ctx) -> None:
        self._log.warning(msg, **ctx)

    def error(self, msg: str, **ctx) -> None:
        self._log.error(msg, **ctx)
