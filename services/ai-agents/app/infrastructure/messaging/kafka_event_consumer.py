from __future__ import annotations

import asyncio
import json
import random
from typing import Sequence

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from ...domain.ports.event_consumer import EventConsumer, EventHandler
from ...domain.ports.logger import Logger

# Ceiling on a single retry delay. With the default base of 0.5s the
# exponential curve hits this around attempt 7 — anything longer just
# stalls the partition without improving the odds the handler recovers.
_MAX_BACKOFF_SECONDS = 30.0


def compute_backoff_delay(
    *,
    attempt: int,
    backoff_seconds: float,
    max_delay: float = _MAX_BACKOFF_SECONDS,
) -> float:
    """Exponential backoff with jitter for retry `attempt` (1-based).

    base * 2^(attempt-1), scaled by a random factor in [0.5, 1.0) so a
    fleet of consumers failing on the same dependency doesn't retry in
    lockstep, capped at `max_delay`. Pure function — unit-testable
    without a broker.
    """
    delay = backoff_seconds * (2 ** (attempt - 1)) * (0.5 + random.random() / 2)
    return min(delay, max_delay)


class KafkaEventConsumer(EventConsumer):
    """
    aiokafka-backed consumer with bounded retry + dead-letter queue.

    For each message:
      1. Look up the handler by `event-name` header.
      2. Try the handler. On exception, retry up to `max_attempts` with
         exponential backoff + jitter (we control the loop so the broker
         doesn't keep re-delivering the same offset).
      3. If still failing, write the original message to `<topic>.dlq`
         and continue. The consumer never gets stuck on a poison message.

    No-op handler (unknown event) is treated as success — we don't DLQ
    things we deliberately don't process.
    """

    def __init__(
        self,
        brokers: list[str],
        group_id: str,
        logger: Logger,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._brokers = brokers
        self._group_id = group_id
        self._logger = logger
        self._max_attempts = max(1, max_attempts)
        self._backoff_seconds = backoff_seconds
        self._handlers: dict[str, EventHandler] = {}
        self._consumer: AIOKafkaConsumer | None = None
        self._dlq_producer: AIOKafkaProducer | None = None
        self._task: asyncio.Task[None] | None = None

    def on(self, event_name: str, handler: EventHandler) -> "KafkaEventConsumer":
        self._handlers[event_name] = handler
        return self

    async def start(self, topics: Sequence[str]) -> None:
        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self._brokers,
            group_id=self._group_id,
            # Commit only after a message is fully processed (or DLQ'd) so
            # crashes don't silently drop messages.
            enable_auto_commit=False,
            auto_offset_reset="latest",
        )
        await self._consumer.start()

        self._dlq_producer = AIOKafkaProducer(
            bootstrap_servers=self._brokers,
            enable_idempotence=True,
            acks="all",
            client_id=f"{self._group_id}-dlq",
        )
        await self._dlq_producer.start()

        self._task = asyncio.create_task(self._run())
        self._logger.info(
            "kafka.consumer.started",
            topics=list(topics),
            group=self._group_id,
            max_attempts=self._max_attempts,
        )
        self._logger.warning(
            "kafka.consumer.offset_reset_latest",
            group=self._group_id,
            detail=(
                "auto_offset_reset='latest': a brand-new consumer group "
                "starts at the END of each topic and skips any existing "
                "backlog — an accidental group_id change silently drops "
                "events published before the new group first connects."
            ),
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # expected: we just cancelled this task
        if self._consumer is not None:
            await self._consumer.stop()
        if self._dlq_producer is not None:
            await self._dlq_producer.stop()

    async def _run(self) -> None:
        assert self._consumer is not None
        async for msg in self._consumer:
            headers = {k: v.decode("utf-8") for k, v in (msg.headers or [])}
            event_name = headers.get("event-name")
            event_id = headers.get("event-id", "")

            handler = self._handlers.get(event_name) if event_name else None
            if not handler:
                # Unknown event or missing header → nothing to do, commit and move on.
                await self._consumer.commit()
                continue

            handled = await self._try_with_retries(msg, headers, handler)
            if not handled:
                await self._send_to_dlq(msg, headers, event_name=event_name, event_id=event_id)
            await self._consumer.commit()

    async def _try_with_retries(self, msg, headers, handler: EventHandler) -> bool:
        for attempt in range(1, self._max_attempts + 1):
            try:
                body = json.loads(msg.value.decode("utf-8"))
                await handler(body)
                return True
            except Exception as err:  # noqa: BLE001
                self._logger.warning(
                    "kafka.handler.attempt_failed",
                    event=headers.get("event-name"),
                    attempt=attempt,
                    of=self._max_attempts,
                    error=str(err),
                )
                if attempt < self._max_attempts:
                    await asyncio.sleep(
                        compute_backoff_delay(
                            attempt=attempt,
                            backoff_seconds=self._backoff_seconds,
                        )
                    )
        return False

    async def _send_to_dlq(
        self,
        msg,
        headers: dict[str, str],
        *,
        event_name: str | None,
        event_id: str,
    ) -> None:
        assert self._dlq_producer is not None
        dlq_topic = f"{msg.topic}.dlq"
        # Preserve original headers + add the failure reason.
        encoded_headers = [(k, v.encode("utf-8")) for k, v in headers.items()]
        encoded_headers.append(("dlq-from-topic", msg.topic.encode("utf-8")))
        encoded_headers.append(("dlq-from-partition", str(msg.partition).encode("utf-8")))
        encoded_headers.append(("dlq-from-offset", str(msg.offset).encode("utf-8")))
        await self._dlq_producer.send_and_wait(
            topic=dlq_topic,
            key=msg.key,
            value=msg.value,
            headers=encoded_headers,
        )
        self._logger.error(
            "kafka.message.dead_lettered",
            topic=msg.topic,
            dlq=dlq_topic,
            event=event_name,
            event_id=event_id,
        )
