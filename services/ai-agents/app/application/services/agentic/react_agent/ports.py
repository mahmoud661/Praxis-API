"""
Ports — the shapes the react_agent library needs from its host.

This package is designed to be extracted as a standalone library. It
therefore owns NO storage, NO HTTP clients, NO app config — only the
graph runtime plus the attachment/content-reference systems built on
top of it. Everything environmental comes in through the Protocols
below: the host application implements them (structurally — no
inheritance required) and passes instances in when it builds an agent
graph. "You can use me — here's what I need."

Hard rule for everything inside `react_agent/`: no imports from the
host application (`app.*`, relative escapes above this package). The
boundary is enforced by a guard test in the host's suite.

Error contract: implementations signal "attachment doesn't exist /
caller can't see it" by raising `AttachmentNotFoundError` (or a
subclass — the host's domain error can inherit from it), and
"can't read this content type" with `UnsupportedContentError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


# ---- errors ------------------------------------------------------------------


class AttachmentNotFoundError(Exception):
    """The attachment id doesn't resolve for this owner. Host file
    services should raise this (or a subclass) from `get`/`read_bytes`
    so the library can degrade gracefully instead of crashing a turn."""


class UnsupportedContentError(Exception):
    """The extractor can't handle this MIME type. Host extractors
    should raise this (or a subclass) from `extract_text` /
    `to_image_block`."""


# ---- attachment shapes -------------------------------------------------------


@runtime_checkable
class AttachmentInfo(Protocol):
    """Metadata for one stored attachment. Structural — any object
    with these attributes satisfies it (the host's file-view DTO
    typically does, unchanged)."""

    @property
    def id(self) -> str: ...
    @property
    def filename(self) -> str: ...
    @property
    def mime_type(self) -> str: ...
    @property
    def size_bytes(self) -> int: ...
    @property
    def caption(self) -> str | None: ...


class AttachmentStore(Protocol):
    """Where attachment bytes + metadata live. The library never knows
    or cares whether this is a local disk, S3, or a database — it only
    asks for these three operations, always owner-scoped."""

    async def get(self, *, file_id: str, owner_id: str) -> AttachmentInfo:
        """Metadata for one file. Raises `AttachmentNotFoundError` for
        a missing file OR a cross-owner request (don't leak existence)."""
        ...

    async def read_bytes(self, *, file_id: str, owner_id: str) -> bytes:
        """Raw bytes. Same error contract as `get`."""
        ...

    async def set_caption(
        self, *, file_id: str, owner_id: str, caption: str
    ) -> None:
        """Persist a generated caption on the file's metadata so later
        evictions reuse it instead of paying another model call."""
        ...


class ContentExtractor(Protocol):
    """Turns raw bytes into model-ingestible content."""

    def extract_text(self, *, data: bytes, mime_type: str) -> str:
        """Plain text from a text-bearing file. Raises
        `UnsupportedContentError` for MIMEs it can't read as text."""
        ...

    def to_image_block(self, *, data: bytes, mime_type: str) -> dict[str, Any]:
        """A multimodal content block (the dict shape the chat client
        accepts inside a message's `content` list) for an image. Raises
        `UnsupportedContentError` for non-image MIMEs."""
        ...

    def supports(self, *, mime_type: str) -> bool:
        """True iff either extraction path can handle this MIME."""
        ...


class CaptionModel(Protocol):
    """One-shot captioning calls used by attachment compaction (stub
    descriptions) and the preload's OCR fallback for text-only agents.
    The host decides which model/proxy serves these; the library only
    needs the two calls. Both return `None` on failure — the library
    falls back to filename-based captions, never raises."""

    async def caption_image(
        self, *, data: bytes, mime_type: str
    ) -> str | None: ...

    async def caption_text(
        self, *, filename: str, preview: str
    ) -> str | None: ...


# ---- misc --------------------------------------------------------------------


class LoggerLike(Protocol):
    """Minimal structured-logging surface. The host's logger satisfies
    this structurally; tests can pass a no-op."""

    def info(self, event: str, **fields: Any) -> None: ...
    def warning(self, event: str, **fields: Any) -> None: ...
    def error(self, event: str, **fields: Any) -> None: ...


@dataclass(frozen=True)
class AttachmentConfig:
    """Tuning knobs for the attachment system. Plain values — the host
    maps its own config/env onto this at graph-build time.

    - `preview_chars`: how much of a text/PDF attachment the preload
      middleware injects up front (the rest is paged via the
      read_attachment tool).
    - `page_chars`: page size per explicit read_attachment call.
    - `keep_turns`: how many of the most recent user turns keep their
      attachments at full fidelity before compaction stubs them.
    """

    preview_chars: int = 4_000
    page_chars: int = 20_000
    keep_turns: int = 3
