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

import asyncio
from typing import Any, Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from ....application.services._errors import FileNotFoundError
from ....domain.IRepos.i_thread_repo import IThreadRepo
from ....domain.IServices.i_files_service import IFilesService
from ....domain.ports.i_memory_client import IMemoryClient
from ....domain.ports.i_projects_client import IProjectsClient, ProjectContext
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
        projects_client: IProjectsClient,
        thread_repo: IThreadRepo,
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
        self._projects = projects_client
        self._thread_repo = thread_repo
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
        # Project this thread is bound to (if any). Lands in the config so the
        # sandbox tools can resolve the project's sandbox themselves instead of
        # the model having to pass a sandbox id.
        project_id = await self._project_id(thread_id)
        config = {
            "configurable": {
                "thread_id": thread_id,
                "owner_id": owner_id,
                # Empty list when no files attached this turn. The
                # middleware keys off list emptiness — present-and-empty
                # is the "user sent text only" signal, missing key would
                # be a config-shape bug.
                "attachments": attachment_ids,
                # None for a normal standalone chat.
                "project_id": project_id,
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
        messages = [HumanMessage(content=user_message, additional_kwargs=human_extra)]

        # On the very first turn of a new thread, inject a SystemMessage with
        # what the graph already knows about this user so the agent is never
        # cold. Subsequent turns inherit the context from the LangGraph checkpoint.
        try:
            state = await graph.aget_state(config)
            is_new_thread = not (state.values or {}).get("messages")
        except Exception:  # noqa: BLE001
            is_new_thread = False

        if is_new_thread:
            try:
                # 3-second cap: a slow Neo4j or memory service must not delay
                # the user's first message by the full 15-second HTTP timeout.
                context = await asyncio.wait_for(
                    self._memory.get_context(owner_id=owner_id),
                    timeout=3.0,
                )
                if context:
                    messages.insert(0, SystemMessage(content=context))
            except Exception:  # noqa: BLE001
                pass  # never block the run on a failed or slow memory fetch

            # If this thread is linked to a project, prime the agent with the
            # project's repo + sandbox so it knows what it's working on and
            # which sandbox id to pass to the sandbox tools. Best-effort and
            # time-boxed — a slow/absent projects service must never delay or
            # fail the run. Inserted at index 0 so it leads the context.
            project_prompt = await self._project_context_prompt(
                project_id=project_id, owner_id=owner_id
            )
            if project_prompt:
                messages.insert(0, SystemMessage(content=project_prompt))

        inputs = {"messages": messages}

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

    async def _project_id(self, thread_id: str) -> str | None:
        """The project this thread is bound to (`config.project_id`), or None."""
        try:
            thread = await self._thread_repo.get(thread_id)
        except Exception:  # noqa: BLE001
            return None
        return thread.config.project_id if thread else None

    async def _project_context_prompt(
        self, *, project_id: str | None, owner_id: str
    ) -> str | None:
        """Build the system-prompt preamble for a project-linked thread:
        the project's name + repo, and a note that the sandbox tools operate
        on this project's sandbox automatically (auto-created on first use).
        Returns `None` for standalone chats or on any failure — the caller
        treats project context as best-effort."""
        if not project_id:
            return None
        try:
            project: ProjectContext | None = await asyncio.wait_for(
                self._projects.get_project(
                    project_id=project_id, owner_id=owner_id
                ),
                timeout=3.0,
            )
        except Exception:  # noqa: BLE001
            return None
        if project is None:
            return None

        lines = [
            f'You are the coding agent for the project "{project.name}".',
        ]
        if project.github_repo_url:
            lines.append(f"GitHub repository: {project.github_repo_url}")
        lines.append(
            "You have a persistent Linux sandbox for this project at "
            "/workspace. The sandbox tools (run_command_in_sandbox, "
            "read/write/list files, get_sandbox_stream_url) operate on it "
            "directly — you do NOT need a sandbox id, and the sandbox is "
            "started automatically the first time you use a tool. Files and "
            "installed packages persist across turns."
        )
        return "\n".join(lines)

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
