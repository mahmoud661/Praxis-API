"""Tests for TurnsService rewind logic.

Focus: retry/edit must preserve the original turn's attachments. The
HumanMessage being rewound carries a metadata snapshot under
`additional_kwargs.attachments`; the re-run has to pass those file ids
to `RunManager.start_run` so the preload middleware primes the model
with the same files. A regression here silently drops the attachment
from the regenerated turn (the model answers without the document).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from app.application.services._errors import InvalidTurnTargetError
from app.application.services.turns_service import TurnsService, _attachment_ids


# ---- fakes -------------------------------------------------------------------


@dataclass
class _FakeThread:
    owner_id: str


class _FakeThreadRepo:
    def __init__(self, owner_id: str) -> None:
        self._thread = _FakeThread(owner_id=owner_id)

    async def get(self, _thread_id: str) -> _FakeThread:
        return self._thread


@dataclass
class _FakeState:
    values: dict[str, Any]


class _FakeGraph:
    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages
        self.removed: list[Any] = []

    async def aget_state(self, _config: dict[str, Any]) -> _FakeState:
        return _FakeState(values={"messages": self._messages})

    async def aupdate_state(
        self, _config: dict[str, Any], update: dict[str, Any]
    ) -> None:
        self.removed.extend(update.get("messages") or [])


class _FakeAgent:
    def __init__(self, graph: _FakeGraph) -> None:
        self._graph = graph

    def get(self) -> _FakeGraph:
        return self._graph


class _FakeRegistry:
    """Stands in for AgentRegistry — TurnsService only calls
    `default_agent().get()`."""

    def __init__(self, graph: _FakeGraph) -> None:
        self._agent = _FakeAgent(graph)

    def default_agent(self) -> _FakeAgent:
        return self._agent


@dataclass
class _StartRunCall:
    thread_id: str
    owner_id: str
    content: str
    attachments: list[str]


class _FakeRunManager:
    def __init__(self) -> None:
        self.calls: list[_StartRunCall] = []

    def is_active(self, _thread_id: str) -> bool:
        return False

    async def start_run(
        self,
        *,
        thread_id: str,
        owner_id: str,
        content: str,
        attachments: list[str] | None = None,
    ) -> bool:
        self.calls.append(
            _StartRunCall(
                thread_id=thread_id,
                owner_id=owner_id,
                content=content,
                attachments=list(attachments or []),
            )
        )
        return True


class _NullLogger:
    def info(self, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, *args: Any, **kwargs: Any) -> None: ...

    def error(self, *args: Any, **kwargs: Any) -> None: ...


def _service(
    messages: list[Any], owner_id: str = "owner-1"
) -> tuple[TurnsService, _FakeGraph, _FakeRunManager]:
    graph = _FakeGraph(messages)
    run_manager = _FakeRunManager()
    service = TurnsService(
        thread_repo=_FakeThreadRepo(owner_id),
        agent_registry=_FakeRegistry(graph),
        run_manager=run_manager,
        logger=_NullLogger(),
    )
    return service, graph, run_manager


PDF_SNAPSHOT = {
    "id": "file-1",
    "filename": "labour_law.pdf",
    "mime_type": "application/pdf",
    "size_bytes": 498200,
}


# ---- _attachment_ids ----------------------------------------------------------


def test_attachment_ids_extracts_snapshot_ids() -> None:
    msg = HumanMessage(
        content="check this",
        additional_kwargs={
            "attachments": [PDF_SNAPSHOT, {"no_id": True}, {"id": ""}]
        },
    )
    assert _attachment_ids(msg) == ["file-1"]


def test_attachment_ids_empty_for_text_only_turn() -> None:
    assert _attachment_ids(HumanMessage(content="plain")) == []


def test_attachment_ids_tolerates_malformed_snapshot() -> None:
    msg = HumanMessage(
        content="x", additional_kwargs={"attachments": "not-a-list"}
    )
    assert _attachment_ids(msg) == []


# ---- retry / edit flow ---------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_passes_original_attachments_to_the_new_run() -> None:
    human = HumanMessage(
        content="check this",
        id="msg-user",
        additional_kwargs={"attachments": [PDF_SNAPSHOT]},
    )
    reply = AIMessage(content="I loaded the document.", id="msg-ai")
    service, graph, run_manager = _service([human, reply])

    await service.retry(
        thread_id="t1", owner_id="owner-1", message_id="msg-user"
    )

    assert len(run_manager.calls) == 1
    call = run_manager.calls[0]
    assert call.content == "check this"
    assert call.attachments == ["file-1"]
    # Both the user message and the reply were removed from state.
    assert {r.id for r in graph.removed} == {"msg-user", "msg-ai"}


@pytest.mark.asyncio
async def test_edit_keeps_attachments_with_the_new_text() -> None:
    human = HumanMessage(
        content="check this",
        id="msg-user",
        additional_kwargs={"attachments": [PDF_SNAPSHOT]},
    )
    service, _graph, run_manager = _service([human])

    await service.edit(
        thread_id="t1",
        owner_id="owner-1",
        message_id="msg-user",
        content="summarize chapter 3",
    )

    call = run_manager.calls[0]
    assert call.content == "summarize chapter 3"
    assert call.attachments == ["file-1"]


@pytest.mark.asyncio
async def test_retry_of_text_only_turn_sends_no_attachments() -> None:
    human = HumanMessage(content="hello", id="msg-user")
    service, _graph, run_manager = _service([human])

    await service.retry(
        thread_id="t1", owner_id="owner-1", message_id="msg-user"
    )

    assert run_manager.calls[0].attachments == []


@pytest.mark.asyncio
async def test_retry_rejects_assistant_target() -> None:
    reply = AIMessage(content="hi", id="msg-ai")
    service, _graph, _run_manager = _service([reply])

    with pytest.raises(InvalidTurnTargetError):
        await service.retry(
            thread_id="t1", owner_id="owner-1", message_id="msg-ai"
        )
