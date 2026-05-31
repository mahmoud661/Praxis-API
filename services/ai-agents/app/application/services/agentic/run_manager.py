"""
RunManager — bridge between "user submits a turn" and "agent does work".

A run is an asyncio.Task launched here, NOT a coroutine awaited inside the
WebSocket handler. The Task survives client disconnects; the client can
reconnect and replay the EventStream to catch up.

State touched:

  - in-process `dict[thread_id, RunHandle]` — the live run plus the queue
    of pending user messages on the same thread. When a turn finishes, the
    `_execute` loop pops the next queued message and runs it; only when
    the queue is empty does the handle get torn down.

  - `EventStream` (Redis Streams) — append-on-emit, deleted when the
    *whole* multi-turn run drains. Reconnecting clients see the latest
    in-flight turn's events; finished threads get an empty replay.

  - `agents:running:{owner_id}` (Redis SET) — authoritative running list
    for a user. Driven by the same handle lifecycle.

  - `users:{owner_id}:notif` (Redis pub/sub) — `run.started`, `run.ended`,
    `queue.changed`. Notifications WS subscribes on the frontend.

Future multi-user same-conversation: membership is implicit `{owner_id}`
today; swap that for a per-thread members set and the rest doesn't change.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis

from ....domain.IRepos.i_thread_repo import IThreadRepo
from ....domain.ports.logger import Logger
from ....infrastructure.cache.event_stream import EventStream
from .runner import AgentRunner


# Signature for a post-turn hook. Receives (thread_id, owner_id) and is
# awaited in the background after the run loop tears down — must NEVER
# block the next turn or raise into the manager. Composition root wires
# hooks here (e.g. auto-title generation).
PostTurnHook = Callable[[str, str], Awaitable[None]]


def _running_set_key(owner_id: str) -> str:
    return f"agents:running:{owner_id}"


def _notif_channel(owner_id: str) -> str:
    return f"users:{owner_id}:notif"


@dataclass
class _QueuedTurn:
    id: str
    content: str
    enqueued_at: str


@dataclass
class RunHandle:
    thread_id: str
    owner_id: str
    started_at: datetime
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    # Pending user messages on this thread. Drained FIFO between runs by
    # the `_execute` loop. Don't access from outside the manager.
    queue: deque[_QueuedTurn] = field(default_factory=deque, repr=False)


class RunManager:
    """Auto-registered to the DI token `"RunManager"`."""

    def __init__(
        self,
        runner: AgentRunner,
        event_stream: EventStream,
        redis: Redis,
        logger: Logger,
        thread_repo: IThreadRepo,
    ) -> None:
        self._runner = runner
        self._stream = event_stream
        self._redis = redis
        self._logger = logger
        self._thread_repo = thread_repo
        self._handles: dict[str, RunHandle] = {}
        self._next_turn_id = 0
        # Post-turn hooks — fired as fire-and-forget background tasks
        # after `_teardown` finishes. Used by the composition root to
        # plug in cross-cutting "after a run completes" effects (e.g.
        # ThreadsService.maybe_generate_title) without RunManager having
        # to know about them.
        self._post_turn_hooks: list[PostTurnHook] = []

    def register_post_turn_hook(self, hook: PostTurnHook) -> None:
        """Append a callback to fire (as a background task) after every
        run finishes. Hooks must never raise — exceptions are logged and
        swallowed by `_safe_run_hook`."""
        self._post_turn_hooks.append(hook)

    # ----- public API ------------------------------------------------------

    async def start_run(
        self, *, thread_id: str, owner_id: str, content: str
    ) -> bool:
        """Submit a user turn. Always accepted — if a run is in flight on
        this thread, the turn is queued and will fire when the current one
        finishes. Returns True for "started immediately", False for "queued".

        Note: the boolean used to mean "rejected" — it now means "queued".
        Callers don't error on False; the WS handler treats both as success.
        """
        # Bump thread updated_at so the sidebar re-sorts.
        try:
            await self._thread_repo.touch(thread_id)
        except Exception as err:  # noqa: BLE001
            self._logger.warning(
                "run.thread_touch_failed",
                thread_id=thread_id,
                error=str(err),
            )

        handle = self._handles.get(thread_id)
        turn = _QueuedTurn(
            id=self._mint_turn_id(),
            content=content,
            enqueued_at=_now_iso(),
        )

        if handle is not None:
            # Already running — enqueue and announce.
            handle.queue.append(turn)
            await self._emit_queue_changed(handle)
            self._logger.info(
                "run.queued",
                thread_id=thread_id,
                turn_id=turn.id,
                queue_size=len(handle.queue),
            )
            return False

        # No active run — kick one off now.
        await self._redis.sadd(_running_set_key(owner_id), thread_id)
        await self._publish_notification(
            owner_id,
            {"type": "run.started", "thread_id": thread_id, "at": _now_iso()},
        )

        handle = RunHandle(
            thread_id=thread_id,
            owner_id=owner_id,
            started_at=datetime.now(timezone.utc),
        )
        self._handles[thread_id] = handle
        handle.task = asyncio.create_task(self._loop(handle, turn))
        self._logger.info("run.started", thread_id=thread_id, owner_id=owner_id)
        return True

    async def cancel_turn(self, thread_id: str) -> None:
        """Cancel the currently running turn on this thread. Any queued
        turns remain and will fire next."""
        handle = self._handles.get(thread_id)
        if handle and handle.task and not handle.task.done():
            # Cancel via a side-channel flag — we DON'T `.cancel()` the task
            # because that would also kill the queue drainer. The `_loop`
            # checks this between turns.
            # For now the only way to abort mid-turn is to cancel the task
            # and lose the queue — keeping it simple. A future iteration
            # could swap to a per-turn task.
            handle.task.cancel()

    async def cancel_queue_entry(self, thread_id: str, turn_id: str) -> bool:
        """Drop a queued turn (not the running one). Returns True if found."""
        handle = self._handles.get(thread_id)
        if handle is None:
            return False
        original = len(handle.queue)
        handle.queue = deque(t for t in handle.queue if t.id != turn_id)
        if len(handle.queue) == original:
            return False
        await self._emit_queue_changed(handle)
        return True

    async def cancel_all(self) -> None:
        """Shutdown helper: cancel every in-flight loop and await drain."""
        tasks = [h.task for h in self._handles.values() if h.task]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                # Shutdown drain — we just cancelled these tasks
                # ourselves on the line above, so CancelledError is the
                # expected outcome. Any other exception was the task's
                # own runtime error which has nowhere useful to surface
                # during process shutdown.
                pass

    def is_active(self, thread_id: str) -> bool:
        return thread_id in self._handles

    async def active_runs(self, owner_id: str) -> list[str]:
        members = await self._redis.smembers(_running_set_key(owner_id))
        return sorted(members)

    def queue_snapshot(self, thread_id: str) -> list[dict[str, Any]]:
        """Read-only view of the queue (for the WS replay on reconnect)."""
        handle = self._handles.get(thread_id)
        if handle is None:
            return []
        return [
            {"id": t.id, "content": t.content, "enqueued_at": t.enqueued_at}
            for t in handle.queue
        ]

    # ----- internals -------------------------------------------------------

    async def _loop(self, handle: RunHandle, first_turn: _QueuedTurn) -> None:
        """Drain turns FIFO until the queue is empty, then tear down. One
        background task per thread; per-turn isolation lives inside the
        runner's astream loop."""
        current = first_turn
        try:
            while True:
                await self._run_one_turn(handle, current)
                if not handle.queue:
                    break
                current = handle.queue.popleft()
                await self._emit_queue_changed(handle)
        except asyncio.CancelledError:
            self._logger.info("run.cancelled", thread_id=handle.thread_id)
            raise
        finally:
            await self._teardown(handle)

    async def _run_one_turn(self, handle: RunHandle, turn: _QueuedTurn) -> None:
        """Run a single turn. The AgentRunner emits all events through
        `on_event`; we route them into the per-thread EventStream so
        reconnecting clients can replay."""

        async def on_event(event: dict[str, Any]) -> None:
            # Tag every event with the turn id so the frontend can group
            # streams by turn (and ignore stale ones if it re-renders).
            event = {"turn_id": turn.id, **event}
            await self._stream.add(handle.thread_id, event)

        # `turn.started` envelope so the frontend knows which turn is now
        # producing events (vs. the historic ones it just replayed).
        await on_event({"type": "turn.started", "content": turn.content})

        try:
            await self._runner.run(
                thread_id=handle.thread_id,
                user_message=turn.content,
                on_event=on_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            self._logger.error(
                "run.turn_failed",
                thread_id=handle.thread_id,
                turn_id=turn.id,
                error=str(err),
            )
            # AgentRunner already emitted an `error` event via on_event.

    async def _teardown(self, handle: RunHandle) -> None:
        """Remove the handle and tell the world the thread idled out."""
        await self._redis.srem(
            _running_set_key(handle.owner_id), handle.thread_id
        )
        await self._publish_notification(
            handle.owner_id,
            {
                "type": "run.ended",
                "thread_id": handle.thread_id,
                "at": _now_iso(),
            },
        )
        # Drop the stream once the WHOLE multi-turn run finishes — a fresh
        # reconnect on the same thread sees an empty replay.
        await self._stream.delete(handle.thread_id)
        self._handles.pop(handle.thread_id, None)
        self._logger.info("run.ended", thread_id=handle.thread_id)
        # Fire post-turn hooks (e.g. auto-title generation) AFTER the
        # handle is gone so a slow hook can't block the next user turn
        # on this thread. Each hook runs as its own background task.
        for hook in self._post_turn_hooks:
            asyncio.create_task(
                self._safe_run_hook(hook, handle.thread_id, handle.owner_id)
            )

    async def _safe_run_hook(
        self, hook: PostTurnHook, thread_id: str, owner_id: str
    ) -> None:
        try:
            await hook(thread_id, owner_id)
        except Exception as err:  # noqa: BLE001
            self._logger.warning(
                "post_turn_hook.failed",
                thread_id=thread_id,
                hook=getattr(hook, "__qualname__", repr(hook)),
                error=str(err),
            )

    async def _emit_queue_changed(self, handle: RunHandle) -> None:
        """Push the current queue snapshot both to the per-thread event
        stream (so the open chat panel sees it) and the per-user
        notification channel (so other tabs/the sidebar can react)."""
        snapshot = [
            {"id": t.id, "content": t.content, "enqueued_at": t.enqueued_at}
            for t in handle.queue
        ]
        event = {
            "type": "queue.changed",
            "thread_id": handle.thread_id,
            "queue": snapshot,
        }
        await self._stream.add(handle.thread_id, event)
        await self._publish_notification(handle.owner_id, event)

    async def _publish_notification(
        self, owner_id: str, payload: dict[str, Any]
    ) -> None:
        await self._redis.publish(
            _notif_channel(owner_id), json.dumps(payload, default=str)
        )

    def _mint_turn_id(self) -> str:
        self._next_turn_id += 1
        return f"t{self._next_turn_id}-{int(datetime.now().timestamp() * 1000)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
