"""
AgentRunner — orchestrates a single graph run.

    graph events → runner → on_event(opaque) → wherever (RunManager → Redis Stream)

Design choices:

  - The runner is the *only* thing that touches the LangGraph stream. The
    consumer (RunManager today; could be anything tomorrow) hands it an
    `on_event` callback and receives opaque dicts. Swap the consumer with
    no runner change.

  - LangChain's astream_events v2 yields rich objects (messages, tool calls,
    intermediate state). We use `langchain_core.load.dumpd` to flatten them
    to a plain dict that JSON-serializes cleanly downstream.

  - Persistence (buffering events for reconnect-replay) is NOT this layer's
    job — the RunManager wires `on_event` to the EventStream. Keeping the
    runner storage-agnostic means a unit test can hand it an in-memory list
    and assert on the events.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain_core.messages import HumanMessage

from ....domain.ports.logger import Logger
from .event_normalizer import normalize_event
from .main_agent import MainAgent

OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


class AgentRunner:
    def __init__(
        self,
        main_agent: MainAgent,
        logger: Logger,
    ) -> None:
        # Container resolves `main_agent: MainAgent` from token "MainAgent".
        self._main_agent = main_agent
        self._logger = logger

    async def run(
        self,
        *,
        thread_id: str,
        user_message: str,
        on_event: OnEvent,
    ) -> None:
        """Stream a single user turn through the graph. Pushes every event
        to `on_event`. Always emits a terminal `run.end` event in `finally`,
        so consumers (and any reconnecting clients via the stream replay)
        always see a clean close — success, error, or cancellation."""
        graph = self._main_agent.get()
        config = {"configurable": {"thread_id": thread_id}}
        inputs = {"messages": [HumanMessage(content=user_message)]}

        self._logger.info("agent.run.start", thread_id=thread_id)
        try:
            async for raw in graph.astream_events(inputs, config=config, version="v2"):
                event = normalize_event(raw)
                if event is None:
                    continue
                await on_event(event)
        except Exception as err:  # noqa: BLE001
            # Surface the error so the client knows the run aborted, then
            # re-raise so the consumer can log/track.
            self._logger.error(
                "agent.run.error", thread_id=thread_id, error=str(err)
            )
            await on_event({"type": "error", "message": str(err)})
            raise
        finally:
            await on_event({"type": "run.end", "thread_id": thread_id})
            self._logger.info("agent.run.end", thread_id=thread_id)
