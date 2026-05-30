"""
EventStream — per-run event bus, backed by Redis Streams.

Why Streams and not list + pub/sub:

    Pub/sub drops messages emitted before a subscriber subscribes (no
    buffering for late joiners). A list with `lrange` + pub/sub has a race
    window: events emitted between "read list" and "subscribe pub/sub" are
    lost. Redis Streams solve both — one key serves "replay from offset 0"
    AND "block waiting for new entries", no duplicates, no misses.

Lifetime: the stream is created on first `add()` and `delete()`-d when the
run ends — per the project's "redis cache clears once the agent finishes"
preference. Subscribers connecting after deletion get an empty replay (which
is the correct signal: "this thread isn't running").

Each entry is a JSON-encoded payload under the single field `payload`.
The auto-generated entry ID (Redis-style `<ms>-<seq>`) is the cursor that
subscribers track to continue from the right place.
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis


def _stream_key(thread_id: str) -> str:
    return f"agents:run:{thread_id}:stream"


class EventStream:
    """Auto-registered to the DI token `"EventStream"`."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def add(self, thread_id: str, event: dict[str, Any]) -> None:
        """Append `event` to the run's stream. Idempotent enough — Redis
        rejects duplicates by entry id only if we pass one; we let it
        auto-generate, so each call is a fresh entry."""
        await self._redis.xadd(
            _stream_key(thread_id),
            {"payload": json.dumps(event, default=str)},
        )

    async def replay(self, thread_id: str, since: str = "0-0") -> list[tuple[str, dict[str, Any]]]:
        """Return all entries with id > `since`, oldest first. Used right
        after a subscriber connects to catch them up to the present moment.

        Returns a list of `(entry_id, event)` tuples so the caller can pass
        the last entry_id to `iter_live()` as the starting cursor."""
        result = await self._redis.xrange(_stream_key(thread_id), min=since, max="+")
        out: list[tuple[str, dict[str, Any]]] = []
        for entry_id, fields in result:
            payload = fields.get("payload")
            if payload is None:
                continue
            out.append((entry_id, json.loads(payload)))
        return out

    async def read_blocking(
        self, thread_id: str, last_id: str, block_ms: int = 5_000
    ) -> list[tuple[str, dict[str, Any]]]:
        """Block up to `block_ms` waiting for new entries after `last_id`.
        Returns the new entries (possibly empty on timeout). The caller
        loops on this to consume the live tail of the stream."""
        result = await self._redis.xread(
            streams={_stream_key(thread_id): last_id},
            count=64,
            block=block_ms,
        )
        if not result:
            return []
        out: list[tuple[str, dict[str, Any]]] = []
        # `result` is [(stream_key, [(entry_id, fields), ...]), ...]; we only
        # asked for one stream, so we just unpack its entries.
        _, entries = result[0]
        for entry_id, fields in entries:
            payload = fields.get("payload")
            if payload is None:
                continue
            out.append((entry_id, json.loads(payload)))
        return out

    async def delete(self, thread_id: str) -> None:
        """Drop the stream. Called by RunManager when the run finishes."""
        await self._redis.delete(_stream_key(thread_id))
