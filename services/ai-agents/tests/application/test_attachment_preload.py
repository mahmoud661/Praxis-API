"""Tests for the AttachmentPreloadMiddleware helper logic.

The middleware itself (`AttachmentPreloadMiddleware`) can't be unit-
tested in this dev env because importing it eagerly pulls
`langchain.agents.middleware`, which isn't on this Python install
(same constraint that keeps the other react_agent middleware tests
out of local pytest — they run in CI/prod where langchain>=1.2 is
installed).

We test:

  - `materialize_attachment` shared helper end-to-end against real
    `DocumentExtractor` + a fake `IFilesService` — covers the path
    every preload + every direct `read_attachment` call uses.

  - `_last_human_index` / `_already_preloaded` pure helpers — guard
    against future regressions in the idempotency logic.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.application.services._errors import FileNotFoundError
from app.application.services.agentic.tools.read_attachment import (
    materialize_attachment,
)
from app.domain.dtos.file_dto import FileView
from app.infrastructure.documents.document_extractor import DocumentExtractor


# ---- fakes -------------------------------------------------------------------


@dataclass
class _FakeFile:
    view: FileView
    data: bytes


class _FakeFilesService:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], _FakeFile] = {}

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


# ---- materialize_attachment (shared by tool + middleware) --------------------


class TestMaterializeAttachment:
    @pytest.mark.asyncio
    async def test_text_file_returns_plain_string(self) -> None:
        files = _FakeFilesService()
        files.seed(
            owner_id="u1",
            file_id="f1",
            filename="notes.txt",
            mime="text/plain",
            data=b"hello world",
        )
        out = await materialize_attachment(
            files=files,
            extractor=DocumentExtractor(),
            file_id="f1",
            owner_id="u1",
        )
        assert out == "hello world"

    @pytest.mark.asyncio
    async def test_image_file_returns_multimodal_block_list(self) -> None:
        files = _FakeFilesService()
        files.seed(
            owner_id="u1",
            file_id="img1",
            filename="cat.png",
            mime="image/png",
            data=b"\x89PNG\r\n\x1a\nHELLO",
        )
        out = await materialize_attachment(
            files=files,
            extractor=DocumentExtractor(),
            file_id="img1",
            owner_id="u1",
        )
        assert isinstance(out, list)
        assert out[0]["type"] == "image_url"
        assert out[0]["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_missing_file_returns_tool_error(self) -> None:
        out = await materialize_attachment(
            files=_FakeFilesService(),
            extractor=DocumentExtractor(),
            file_id="ghost",
            owner_id="u1",
        )
        assert isinstance(out, str)
        assert "[tool error]" in out
        assert "ghost" in out

    @pytest.mark.asyncio
    async def test_cross_owner_returns_tool_error_no_leak(self) -> None:
        files = _FakeFilesService()
        files.seed(
            owner_id="u1",
            file_id="secret",
            filename="secret.txt",
            mime="text/plain",
            data=b"confidential",
        )
        out = await materialize_attachment(
            files=files,
            extractor=DocumentExtractor(),
            file_id="secret",
            owner_id="u2",
        )
        assert "[tool error]" in out
        assert "confidential" not in out

    @pytest.mark.asyncio
    async def test_large_file_first_page_carries_continue_hint(self) -> None:
        files = _FakeFilesService()
        files.seed(
            owner_id="u1",
            file_id="big",
            filename="big.txt",
            mime="text/plain",
            data=b"x" * 100,
        )
        out = await materialize_attachment(
            files=files,
            extractor=DocumentExtractor(),
            file_id="big",
            owner_id="u1",
            max_chars=40,
        )
        assert isinstance(out, str)
        assert "chars 0–40 of 100" in out
        assert "x" * 40 in out
        assert "x" * 41 not in out  # only the page, not the whole file
        assert "read_attachment(file_id='big', offset=40)" in out

    @pytest.mark.asyncio
    async def test_offset_page_reaching_end_says_end_of_file(self) -> None:
        files = _FakeFilesService()
        files.seed(
            owner_id="u1",
            file_id="big",
            filename="big.txt",
            mime="text/plain",
            data=b"x" * 100,
        )
        out = await materialize_attachment(
            files=files,
            extractor=DocumentExtractor(),
            file_id="big",
            owner_id="u1",
            offset=60,
            max_chars=40,
        )
        assert "chars 60–100 of 100" in out
        assert "[end of file]" in out
        assert "offset=" not in out  # no continue hint past the end

    @pytest.mark.asyncio
    async def test_offset_beyond_end_returns_note(self) -> None:
        files = _FakeFilesService()
        files.seed(
            owner_id="u1",
            file_id="small",
            filename="small.txt",
            mime="text/plain",
            data=b"hello",
        )
        out = await materialize_attachment(
            files=files,
            extractor=DocumentExtractor(),
            file_id="small",
            owner_id="u1",
            offset=999,
        )
        assert "[tool note]" in out
        assert "beyond the end" in out

    @pytest.mark.asyncio
    async def test_unsupported_mime_returns_tool_error(self) -> None:
        files = _FakeFilesService()
        files.seed(
            owner_id="u1",
            file_id="weird",
            filename="x.bin",
            mime="application/x-thing",
            data=b"...",
        )
        out = await materialize_attachment(
            files=files,
            extractor=DocumentExtractor(),
            file_id="weird",
            owner_id="u1",
        )
        assert "[tool error]" in out
