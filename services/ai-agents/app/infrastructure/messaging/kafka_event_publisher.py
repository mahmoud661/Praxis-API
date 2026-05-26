from __future__ import annotations

import json
from typing import Sequence

from aiokafka import AIOKafkaProducer

from ...domain.ports.logger import Logger
from ...domain.shared.domain_event import DomainEvent


class KafkaEventPublisher:
    """aiokafka-backed implementation of the EventPublisher port."""

    def __init__(self, brokers: list[str], client_id: str, logger: Logger) -> None:
        self._brokers = brokers
        self._client_id = client_id
        self._logger = logger
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        p = AIOKafkaProducer(
            bootstrap_servers=self._brokers,
            enable_idempotence=True,
            acks="all",
            compression_type="gzip",
            client_id=self._client_id,
        )
        await p.start()
        self._producer = p
        self._logger.info("kafka.producer.connected")

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: str, events: Sequence[DomainEvent]) -> None:
        if not events:
            return
        if self._producer is None:
            await self.start()
        assert self._producer is not None
        for event in events:
            envelope = {
                "metadata": {
                    "eventId": event.metadata.event_id,
                    "occurredAt": event.metadata.occurred_at,
                    "eventName": event.metadata.event_name,
                    "aggregateId": event.metadata.aggregate_id,
                    "version": event.metadata.version,
                },
                "payload": event.payload,
            }
            await self._producer.send_and_wait(
                topic=topic,
                key=event.metadata.aggregate_id.encode("utf-8"),
                value=json.dumps(envelope).encode("utf-8"),
                headers=[
                    ("event-name", event.metadata.event_name.encode("utf-8")),
                    ("event-id", event.metadata.event_id.encode("utf-8")),
                    ("event-version", str(event.metadata.version).encode("utf-8")),
                ],
            )
