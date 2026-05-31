"""
WebSocket transport for a single agent thread. Mounted at /ws/agents/{tid}.

What a connection does:

  1. Authenticate via `X-User-Id` (forwarded by the gateway). Reject with
     WS 1008 if missing.
  2. Accept the socket.
  3. **Replay** every entry currently in the thread's EventStream so a late
     joiner catches up to live (including a client reconnecting after a
     transient network drop while a run is in flight).
  4. Split into two concurrent tasks:
       - reader: drain client messages — `{"content": "..."}` starts a
         new run via RunManager (if no run is active for this thread).
       - writer: `XREAD BLOCK` the stream from the last replayed offset
         and forward each new event to the client.
  5. On disconnect, cancel both tasks. The RunManager run is independent
     and keeps going — that's the whole point of decoupling.

The route is **opaque to event content** — it forwards whatever the
RunManager wrote to the stream. Swap the agent, the runner, or the state
machine behind the manager and this handler doesn't change.
"""

from __future__ import annotations

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from ...application.services.agentic.run_manager import RunManager
from ...infrastructure.cache.event_stream import EventStream
from ..http.dependencies import ws_authenticate
from .base_route import BaseRoute


# `XREAD BLOCK` timeout, in milliseconds. Picking 5s is the usual trade-off:
# short enough that a graceful cancel is responsive, long enough that we
# don't burn CPU re-issuing the call.
_READ_BLOCK_MS = 5_000


class AgentsWsRoute(BaseRoute):
    path = "/ws"

    def __init__(
        self,
        run_manager: RunManager,
        event_stream: EventStream,
    ) -> None:
        # Container resolves these by their annotation class names.
        self._runs = run_manager
        self._stream = event_stream
        super().__init__()

    def _init_routes(self) -> None:
        self.router.add_api_websocket_route(
            "/agents/{thread_id}", self._handle
        )

    async def _handle(self, ws: WebSocket, thread_id: str) -> None:
        user_id = await ws_authenticate(ws)
        if user_id is None:
            return  # ws_authenticate already closed the socket

        await ws.accept()

        # --- replay: catch the client up to "now" --------------------------
        last_id = "0"
        for entry_id, event in await self._stream.replay(thread_id):
            await ws.send_json(event)
            last_id = entry_id

        # --- live tail + inbound user input, concurrently -----------------
        # We use a small queue + two tasks rather than nested awaits so a
        # write to the WS doesn't block reading the next user message and
        # vice versa.
        async def reader() -> None:
            while True:
                msg = await ws.receive_json()
                content = str(msg.get("content") or "").strip()
                if not content:
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": "expected {content: str}",
                        }
                    )
                    continue
                # Always accepted — if a run is in flight the manager
                # appends to its FIFO queue and emits `queue.changed` over
                # the stream so the UI can render the pending turn.
                await self._runs.start_run(
                    thread_id=thread_id,
                    owner_id=user_id,
                    content=content,
                )

        async def writer() -> None:
            nonlocal last_id
            while True:
                # `read_blocking` returns [] on timeout — loop and try again.
                # When the thread isn't running, this just idles. When it
                # IS running, entries flow as the RunManager XADDs.
                batch = await self._stream.read_blocking(
                    thread_id, last_id, block_ms=_READ_BLOCK_MS
                )
                for entry_id, event in batch:
                    await ws.send_json(event)
                    last_id = entry_id

        reader_task = asyncio.create_task(reader())
        writer_task = asyncio.create_task(writer())
        try:
            done, pending = await asyncio.wait(
                {reader_task, writer_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            # Surface any non-disconnect exception to the logs by awaiting
            # the completed task(s). WebSocketDisconnect is the common case.
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        except WebSocketDisconnect:
            # Client closed the socket — the expected, normal outcome
            # of any chat session. No diagnostic value in logging.
            pass
        finally:
            for task in (reader_task, writer_task):
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        # Cleanup drain: the task was cancelled by the
                        # line above; CancelledError is what we expect.
                        # Any other error came from the task and has
                        # nowhere useful to surface from cleanup.
                        pass
            # IMPORTANT: do NOT cancel the run. Disconnect ≠ cancel — the
            # whole point is the run keeps going so the user can reconnect.
