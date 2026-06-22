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

from ....application.services._errors import FileNotFoundError
from ....domain.IServices.i_files_service import IFilesService
from ....domain.ports.i_memory_client import IMemoryClient
from ....domain.ports.logger import Logger
from .agent_registry import AgentRegistry
from .event_normalizer import normalize_event

OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


class AgentRunner:
    def __init__(
        self,
        agent_registry: AgentRegistry,
        files: IFilesService,
        memory_client: IMemoryClient,
        logger: Logger,
    ) -> None:
        # The runner's only entry into the agentic stack is through an
        # AGENT resolved from the registry — it never imports or calls
        # the react_agent runtime itself (that's the agent's job, in
        # its graph.py). Today every thread runs the default agent;
        # per-thread `agent_id` plugs in here later.
        # `files` is needed to snapshot attachment metadata onto the
        # persisted HumanMessage so the frontend can render the chips
        # after a reload (file might be deleted later — the snapshot
        # still tells the UI "this was attached: report.pdf").
        self._registry = agent_registry
        self._files = files
        self._memory = memory_client
        self._logger = logger

    async def run(
        self,
        *,
        thread_id: str,
        owner_id: str,
        user_message: str,
        attachments: list[str] | None = None,
        on_event: OnEvent,
    ) -> None:
        """Stream a single user turn through the graph. Pushes every event
        to `on_event`. Always emits a terminal `run.end` event in `finally`,
        so consumers (and any reconnecting clients via the stream replay)
        always see a clean close — success, error, or cancellation.

        `owner_id` lands in the LangGraph config so tools (read_attachment,
        kb_search) can resolve per-user scope. `attachments` is the list
        of file ids the user attached for THIS turn; the preload middleware
        picks them up and synthesizes a fake `read_attachment` tool call
        per id so the model sees the file content as if it had fetched it.
        """
        graph = self._registry.default_agent().get()
        attachment_ids = list(attachments or [])
        config = {
            "configurable": {
                "thread_id": thread_id,
                "owner_id": owner_id,
                # Empty list when no files attached this turn. The
                # middleware keys off list emptiness — present-and-empty
                # is the "user sent text only" signal, missing key would
                # be a config-shape bug.
                "attachments": attachment_ids,
            }
        }
        # Link each attachment to this conversation in the knowledge graph.
        # Fire-and-forget — a failure must not block the agent run.
        for file_id in attachment_ids:
            try:
                await self._memory.provision_link(
                    from_id=thread_id,
                    to_id=file_id,
                    owner_id=owner_id,
                    relationship="HAS_ATTACHMENT",
                )
            except Exception:  # noqa: BLE001
                pass

        # Snapshot file metadata into the message itself so reloaded
        # history can render attachment chips without an extra round-
        # trip per file, AND so the chip still shows even if the file
        # is later deleted by the user. Snapshot is best-effort: ids
        # we can't resolve (cross-owner, deleted between upload and
        # send) are silently dropped from the snapshot.
        attachments_meta = await self._snapshot_attachments(
            owner_id=owner_id, file_ids=attachment_ids
        )
        human_extra: dict[str, Any] = {}
        if attachments_meta:
            human_extra["attachments"] = attachments_meta
        inputs = {
            "messages": [
                HumanMessage(content=user_message, additional_kwargs=human_extra)
            ]
        }

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

    async def _snapshot_attachments(
        self, *, owner_id: str, file_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Resolve each file id to a small metadata snapshot the
        message persists alongside its text content."""
        out: list[dict[str, Any]] = []
        for file_id in file_ids:
            try:
                view = await self._files.get(file_id=file_id, owner_id=owner_id)
            except FileNotFoundError:
                self._logger.warning(
                    "agent.run.attachment_missing",
                    file_id=file_id,
                    owner_id=owner_id,
                )
                continue
            out.append(
                {
                    "id": view.id,
                    "filename": view.filename,
                    "mime_type": view.mime_type,
                    "size_bytes": view.size_bytes,
                }
            )
        return out
