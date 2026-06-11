"""Tests for the agent tools: `read_attachment` and `kb_search`.

LangChain tools are invoked via `.ainvoke({...args, "config": ...})`.
We exercise the same path the real agent runtime would take.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.application.services._errors import FileNotFoundError
from app.application.services.agentic.tools.kb_search import make_kb_search_tool
from app.application.services.agentic.tools.read_attachment import (
    make_read_attachment_tool,
)
from app.domain.dtos.content_reference_dto import AttachmentRef, WebpageRef
from app.domain.dtos.file_dto import FileView
from app.domain.dtos.knowledge_dto import KnowledgeChunk, KnowledgeSearchHit
from app.infrastructure.documents.document_extractor import DocumentExtractor


# ----- shared fakes -----------------------------------------------------------


@dataclass
class _FakeFile:
    view: FileView
    data: bytes


class _FakeFilesService:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], _FakeFile] = {}  # (owner_id, file_id) → file

    async def upload(self, **_kwargs: object) -> FileView:  # pragma: no cover
        raise NotImplementedError

    async def get(self, *, file_id: str, owner_id: str) -> FileView:
        item = self.items.get((owner_id, file_id))
        if item is None:
            raise FileNotFoundError(file_id)
        return item.view

    async def read_bytes(self, *, file_id: str, owner_id: str) -> bytes:
        item = self.items.get((owner_id, file_id))
        if item is None:
            raise FileNotFoundError(file_id)
        return item.data

    async def delete(self, **_kwargs: object) -> None:  # pragma: no cover
        raise NotImplementedError

    async def set_caption(
        self, *, file_id: str, owner_id: str, caption: str
    ) -> None:  # pragma: no cover
        del file_id, owner_id, caption

    def seed(
        self, *, owner_id: str, file_id: str, filename: str, mime: str, data: bytes
    ) -> None:
        self.items[(owner_id, file_id)] = _FakeFile(
            view=FileView(
                id=file_id,
                owner_id=owner_id,
                filename=filename,
                mime_type=mime,
                size_bytes=len(data),
                created_at="2026-06-06T00:00:00Z",
            ),
            data=data,
        )


class _FakeContentReferenceLookup:
    """Pass-through lookup — tests that don't exercise alias resolution
    use this so `make_read_attachment_tool` can construct. Tests that
    DO exercise alias resolution seed `attachments`."""

    def __init__(self) -> None:
        self.attachments: dict[
            tuple[str, int, str, int], AttachmentRef
        ] = {}

    async def resolve_attachment(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> AttachmentRef | None:
        del owner_id
        return self.attachments.get(
            (thread_id, turn_index, category, item_index)
        )

    async def resolve_webpage(self, **_kwargs: object) -> WebpageRef | None:
        return None


class _FakeAgenticStore:
    """Minimal AgenticStore stand-in — kb_search calls `store.aput`;
    tests don't inspect what's written."""

    class _Store:
        async def aput(self, *_args: object, **_kwargs: object) -> None:
            return None

    def __init__(self) -> None:
        self.store = _FakeAgenticStore._Store()


_LOOKUP = _FakeContentReferenceLookup()
_AGENTIC_STORE = _FakeAgenticStore()


def _config(owner_id: str | None = "user-A") -> dict:
    if owner_id is None:
        return {"configurable": {}}
    return {"configurable": {"owner_id": owner_id}}


# ----- read_attachment --------------------------------------------------------


class TestReadAttachment:
    @pytest.mark.asyncio
    async def test_text_file_returns_plain_text(self) -> None:
        files = _FakeFilesService()
        files.seed(
            owner_id="user-A",
            file_id="f1",
            filename="notes.txt",
            mime="text/plain",
            data=b"hello world",
        )
        tool = make_read_attachment_tool(
            files=files, extractor=DocumentExtractor(), lookup=_LOOKUP
        )
        out = await tool.ainvoke({"file_id": "f1"}, config=_config())
        assert out == "hello world"

    @pytest.mark.asyncio
    async def test_image_file_returns_multimodal_content_block(self) -> None:
        files = _FakeFilesService()
        raw = b"\x89PNG\r\n\x1a\nHELLO"
        files.seed(
            owner_id="user-A",
            file_id="img1",
            filename="cat.png",
            mime="image/png",
            data=raw,
        )
        tool = make_read_attachment_tool(
            files=files, extractor=DocumentExtractor(), lookup=_LOOKUP
        )
        out = await tool.ainvoke({"file_id": "img1"}, config=_config())
        assert isinstance(out, list)
        assert out[0]["type"] == "image_url"
        assert out[0]["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_unknown_file_returns_tool_error(self) -> None:
        files = _FakeFilesService()
        tool = make_read_attachment_tool(
            files=files, extractor=DocumentExtractor(), lookup=_LOOKUP
        )
        out = await tool.ainvoke(
            {"file_id": "missing"}, config=_config()
        )
        assert "[tool error]" in out
        assert "missing" in out

    @pytest.mark.asyncio
    async def test_missing_owner_id_returns_tool_error(self) -> None:
        files = _FakeFilesService()
        tool = make_read_attachment_tool(
            files=files, extractor=DocumentExtractor(), lookup=_LOOKUP
        )
        out = await tool.ainvoke(
            {"file_id": "anything"}, config=_config(owner_id=None)
        )
        assert "[tool error]" in out
        assert "owner_id" in out

    @pytest.mark.asyncio
    async def test_cross_owner_access_returns_not_found(self) -> None:
        # Foreign owner mustn't be able to fetch another user's file —
        # IFilesService.get raises FileNotFoundError on owner mismatch,
        # and the tool translates that to a generic not-found message
        # (no existence leak).
        files = _FakeFilesService()
        files.seed(
            owner_id="user-A",
            file_id="secret",
            filename="secret.txt",
            mime="text/plain",
            data=b"confidential",
        )
        tool = make_read_attachment_tool(
            files=files, extractor=DocumentExtractor(), lookup=_LOOKUP
        )
        out = await tool.ainvoke(
            {"file_id": "secret"}, config=_config(owner_id="user-B")
        )
        assert "[tool error]" in out
        assert "confidential" not in out

    @pytest.mark.asyncio
    async def test_empty_pdf_returns_tool_note(self) -> None:
        # An image-only PDF extracts to "" — the tool surfaces that
        # explicitly so the model doesn't pretend it read content.
        from pypdf import PdfWriter
        import io

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buf = io.BytesIO()
        writer.write(buf)

        files = _FakeFilesService()
        files.seed(
            owner_id="user-A",
            file_id="scan",
            filename="scan.pdf",
            mime="application/pdf",
            data=buf.getvalue(),
        )
        tool = make_read_attachment_tool(
            files=files, extractor=DocumentExtractor(), lookup=_LOOKUP
        )
        out = await tool.ainvoke({"file_id": "scan"}, config=_config())
        assert "[tool note]" in out
        assert "scan.pdf" in out

    @pytest.mark.asyncio
    async def test_unsupported_mime_returns_graceful_note(self) -> None:
        # Unknown binary types (audio, video, archives…) must not break
        # the turn — the model gets a descriptive note, not an error.
        files = _FakeFilesService()
        files.seed(
            owner_id="user-A",
            file_id="weird",
            filename="x.bin",
            mime="application/x-thing",
            data=b"...",
        )
        tool = make_read_attachment_tool(
            files=files, extractor=DocumentExtractor(), lookup=_LOOKUP
        )
        out = await tool.ainvoke({"file_id": "weird"}, config=_config())
        assert "[tool error]" not in out
        assert "x.bin" in out
        assert "can't be read as text" in out


# ----- kb_search --------------------------------------------------------------


class _FakeKnowledgeService:
    def __init__(self) -> None:
        self.hits: list[KnowledgeSearchHit] = []
        self.calls: list[tuple[str, str, int]] = []

    async def ingest_file(self, **_kwargs: object) -> int:  # pragma: no cover
        return 0

    async def search(
        self, *, owner_id: str, query: str, k: int = 5
    ) -> list[KnowledgeSearchHit]:
        self.calls.append((owner_id, query, k))
        return self.hits

    async def delete_file_chunks(
        self, **_kwargs: object
    ) -> None:  # pragma: no cover
        return None


def _hit(*, text: str, filename: str, idx: int) -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        chunk=KnowledgeChunk(
            id=f"f:{idx}",
            owner_id="user-A",
            file_id="f",
            chunk_index=idx,
            text=text,
            extra={"filename": filename},
        ),
        score=1.0 - 0.1 * idx,
    )


class TestKbSearch:
    @pytest.mark.asyncio
    async def test_returns_formatted_hits_with_citation_aliases(self) -> None:
        ks = _FakeKnowledgeService()
        ks.hits = [
            _hit(text="Apples are red.", filename="fruit.md", idx=0),
            _hit(text="Oranges are tangy.", filename="fruit.md", idx=1),
        ]
        tool = make_kb_search_tool(
            knowledge_service=ks, agentic_store=_AGENTIC_STORE
        )
        out = await tool.ainvoke(
            {
                "name": "kb_search",
                "args": {"query": "apple"},
                "id": "test-call-1",
                "type": "tool_call",
            },
            config=_config(),
        )
        # Each hit numbered, with filename + body + alias.
        # When invoked with a full tool_call dict, ainvoke returns a
        # ToolMessage whose `.content` is the tool's string return.
        text = out.content if hasattr(out, "content") else out
        assert "[1] fruit.md" in text
        assert "Apples are red." in text
        # 1-indexed citation aliases (matches ChatGPT's convention and
        # what `read_attachment(turn{N}image{M})` accepts).
        assert "citeturn0search1" in text
        assert "[2] fruit.md" in text
        assert "citeturn0search2" in text

    @pytest.mark.asyncio
    async def test_empty_query_returns_tool_error_without_calling_service(
        self,
    ) -> None:
        ks = _FakeKnowledgeService()
        tool = make_kb_search_tool(
            knowledge_service=ks, agentic_store=_AGENTIC_STORE
        )
        out = await tool.ainvoke(
            {
                "name": "kb_search",
                "args": {"query": "   "},
                "id": "test-call-2",
                "type": "tool_call",
            },
            config=_config(),
        )
        text = out.content if hasattr(out, "content") else out
        assert "[tool error]" in text
        assert ks.calls == []

    @pytest.mark.asyncio
    async def test_no_hits_returns_tool_note(self) -> None:
        ks = _FakeKnowledgeService()
        # ks.hits = [] by default
        tool = make_kb_search_tool(
            knowledge_service=ks, agentic_store=_AGENTIC_STORE
        )
        out = await tool.ainvoke(
            {
                "name": "kb_search",
                "args": {"query": "obscure"},
                "id": "test-call-3",
                "type": "tool_call",
            },
            config=_config(),
        )
        text = out.content if hasattr(out, "content") else out
        assert "[tool note]" in text

    @pytest.mark.asyncio
    async def test_missing_owner_id_returns_tool_error(self) -> None:
        ks = _FakeKnowledgeService()
        tool = make_kb_search_tool(
            knowledge_service=ks, agentic_store=_AGENTIC_STORE
        )
        out = await tool.ainvoke(
            {
                "name": "kb_search",
                "args": {"query": "anything"},
                "id": "test-call-no-owner",
                "type": "tool_call",
            },
            config=_config(owner_id=None),
        )
        text = out.content if hasattr(out, "content") else out
        assert "[tool error]" in text
        assert "owner_id" in text
        assert ks.calls == []

    @pytest.mark.asyncio
    async def test_passes_owner_id_through_to_service(self) -> None:
        ks = _FakeKnowledgeService()
        tool = make_kb_search_tool(
            knowledge_service=ks, agentic_store=_AGENTIC_STORE
        )
        await tool.ainvoke(
            {
                "name": "kb_search",
                "args": {"query": "anything"},
                "id": "test-call-user-x",
                "type": "tool_call",
            },
            config=_config(owner_id="user-X"),
        )
        assert ks.calls == [("user-X", "anything", 5)]
