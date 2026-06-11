"""Tests for `DocumentExtractor`.

Plaintext + image tests are exhaustive. PDF tests construct a real
single-page PDF via `pypdf` itself so we exercise the round-trip
without committing a binary fixture.
"""

from __future__ import annotations

import base64
import io

import pytest
from pypdf import PdfReader, PdfWriter

from app.application.services._errors import UnsupportedMimeTypeError
from app.infrastructure.documents.document_extractor import DocumentExtractor


def _empty_pdf_bytes() -> bytes:
    """One-page PDF with no text layer. `extract_text()` should return
    an empty string for it (we can't get pypdf to STAMP text reliably
    without a font file, so we test the empty-page path — that's the
    one our caller needs to handle for image-only scans anyway)."""
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestExtractText:
    def test_plaintext_decodes_as_utf8(self) -> None:
        ex = DocumentExtractor()
        out = ex.extract_text(data=b"hello world", mime_type="text/plain")
        assert out == "hello world"

    def test_markdown_decodes_the_same(self) -> None:
        ex = DocumentExtractor()
        out = ex.extract_text(data=b"# title", mime_type="text/markdown")
        assert out == "# title"

    def test_json_decodes(self) -> None:
        ex = DocumentExtractor()
        out = ex.extract_text(
            data=b'{"k": 1}', mime_type="application/json"
        )
        assert out == '{"k": 1}'

    def test_non_utf8_bytes_replace_rather_than_raise(self) -> None:
        # We'd rather show garbled output than fail a turn over a stray
        # 0x80 in a CSV. errors="replace" leaves a U+FFFD in its place.
        ex = DocumentExtractor()
        out = ex.extract_text(data=b"a\x80b", mime_type="text/plain")
        assert "�" in out

    def test_empty_pdf_returns_empty_string(self) -> None:
        # Image-only scans have no text layer — pypdf returns "" per
        # page. We join with "\n\n", so the overall result is also "".
        ex = DocumentExtractor()
        pdf = _empty_pdf_bytes()
        # Sanity check the fixture has the right shape.
        assert PdfReader(io.BytesIO(pdf)).pages
        out = ex.extract_text(data=pdf, mime_type="application/pdf")
        assert out == ""

    def test_image_mime_raises_use_to_image_block_instead(self) -> None:
        ex = DocumentExtractor()
        with pytest.raises(UnsupportedMimeTypeError):
            ex.extract_text(data=b"\x89PNG\r\n\x1a\n", mime_type="image/png")

    def test_unknown_mime_raises(self) -> None:
        ex = DocumentExtractor()
        with pytest.raises(UnsupportedMimeTypeError):
            ex.extract_text(data=b"...", mime_type="application/x-thing")


class TestToImageBlock:
    def test_png_returns_openai_shaped_block(self) -> None:
        ex = DocumentExtractor()
        raw = b"\x89PNG\r\n\x1a\nHELLO"
        block = ex.to_image_block(data=raw, mime_type="image/png")
        # OpenAI / LangChain vision content-block shape. LiteLLM
        # normalises this to whatever the upstream provider wants.
        assert block["type"] == "image_url"
        assert block["image_url"]["url"] == (
            f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"
        )

    def test_non_image_raises(self) -> None:
        ex = DocumentExtractor()
        with pytest.raises(UnsupportedMimeTypeError):
            ex.to_image_block(data=b"%PDF-1.4", mime_type="application/pdf")


class TestSupports:
    @pytest.mark.parametrize(
        "mime",
        [
            "text/plain",
            "text/markdown",
            "text/csv",
            "application/json",
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
        ],
    )
    def test_supported_mimes(self, mime: str) -> None:
        assert DocumentExtractor().supports(mime_type=mime) is True

    @pytest.mark.parametrize(
        "mime",
        [
            "audio/mpeg",
            "video/mp4",
            "application/x-executable",
            "",
        ],
    )
    def test_unsupported_mimes(self, mime: str) -> None:
        assert DocumentExtractor().supports(mime_type=mime) is False
