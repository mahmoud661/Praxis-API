"""
`read_attachment` — LangChain `@tool` that materializes an uploaded
file into the model's context. Part of the react_agent library base:
it depends only on the ports in `react_agent.ports`, never on host
storage directly.

Per-MIME dispatch:

  - text-like → returns a PAGE of plain text (`offset` + page size).
    Small files come back whole; large files come back as a slice
    with a footer telling the model the next offset to request.
  - PDF → same pagination over the extracted text
  - image (jpeg/png/webp/gif) → returns the multimodal `content_block`
    shape so vision-capable models can "see" the image.
  - anything else (audio, video, archives, binaries) → a descriptive
    note (name, type, size) instead of an error — the turn never breaks.

Accepted `file_id` formats:

  - raw UUID hex — the durable file id
  - model-facing alias `turn{N}{cat}{M}` — resolved against thread
    history via the `ReferenceLookup` port. Lets the model say
    `read_attachment("turn3image1")` instead of memorising a UUID.

Owner / thread ids come from the LangChain `RunnableConfig` the
executor passes to every tool call; the host seeds
`config["configurable"]["owner_id"]` + `["thread_id"]` at run start.

Constructed via `make_read_attachment_tool(store, extractor, lookup)`
— the factory captures the injected ports in the tool's closure.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from ..ports import (
    AttachmentNotFoundError,
    AttachmentStore,
    ContentExtractor,
    UnsupportedContentError,
)
from ..references import ATTACHMENT_CATEGORIES, ReferenceLookup

# Regex matching the model-facing alias. The category alternation is
# built from `ATTACHMENT_CATEGORIES` (the grammar's single source of
# truth in `react_agent.references`), so adding a new MIME bucket there
# automatically teaches this tool to resolve its aliases.
_ALIAS_CATEGORY_ALTS = "|".join(sorted(ATTACHMENT_CATEGORIES))
_ALIAS_RE = re.compile(rf"^turn(\d+)({_ALIAS_CATEGORY_ALTS})(\d+)$")


# ---- module constants --------------------------------------------------------

# Default page size (chars) per call when the caller doesn't specify
# one. A 100MB CSV would otherwise drop straight into the model
# context. Hosts override via `AttachmentConfig.page_chars` (tool
# calls) and `AttachmentConfig.preview_chars` (preload injection).
_DEFAULT_PAGE_CHARS = 20_000


# ---- factory -----------------------------------------------------------------


def make_read_attachment_tool(
    *,
    store: AttachmentStore,
    extractor: ContentExtractor,
    lookup: ReferenceLookup,
    page_chars: int = _DEFAULT_PAGE_CHARS,
) -> BaseTool:
    """Build the tool with the host's ports captured in its closure.
    `page_chars` caps how much text one call returns. Returns the
    BaseTool ready to bind into the agent's `tools=[...]` list."""

    @tool
    async def read_attachment(
        file_id: Annotated[
            str,
            "Id of the uploaded file to read. Either a raw UUID hex "
            "or a model-facing alias like `turn3image1` (turn 3's "
            "first image, etc).",
        ],
        config: RunnableConfig,
        offset: Annotated[
            int,
            "Character offset to start reading from. Defaults to 0 "
            "(start of file). Large files are returned one page at a "
            "time — the end of each page tells you the next offset to "
            "request. Only pass an offset a previous result gave you.",
        ] = 0,
    ) -> str | list[dict[str, Any]]:
        """Read an uploaded file the user attached to this conversation.

        For text and PDF files, returns the file's text content one
        page at a time — if a previous result (or an attachment
        preview) said more content remains, call again with the
        suggested `offset` to continue reading. For images, returns a
        multimodal content block so vision-capable models can see the
        image. The id can be either a raw UUID or an alias of the form
        `turn{N}{kind}{M}` (e.g. `turn3image1`).
        """
        owner_id = _owner_id_from_config(config)
        if owner_id is None:
            return (
                "[tool error] missing owner_id on the run config — "
                "cannot resolve file ownership."
            )
        resolved_id = await _resolve_alias(
            raw=file_id, config=config, owner_id=owner_id, lookup=lookup
        )
        if resolved_id is None:
            return (
                f"[tool error] alias {file_id!r} doesn't match any "
                "attachment in this conversation."
            )
        return await materialize_attachment(
            store=store,
            extractor=extractor,
            file_id=resolved_id,
            owner_id=owner_id,
            offset=offset,
            max_chars=page_chars,
        )

    return read_attachment


async def _resolve_alias(
    *,
    raw: str,
    config: RunnableConfig | None,
    owner_id: str,
    lookup: ReferenceLookup,
) -> str | None:
    """If `raw` looks like `turnNcatM`, resolve via the lookup. Else
    treat as a raw file id (the store will reject an unknown one).
    Returns the canonical file_id or None when the alias points at
    nothing."""
    match = _ALIAS_RE.match(raw)
    if not match:
        return raw  # raw UUID path; the store 404s if wrong
    turn_index = int(match.group(1))
    category = match.group(2)
    item_index = int(match.group(3))  # 1-indexed; lookup converts internally
    thread_id = _thread_id_from_config(config)
    if thread_id is None:
        return None
    payload = await lookup.resolve_attachment(
        thread_id=thread_id,
        owner_id=owner_id,
        turn_index=turn_index,
        category=category,
        item_index=item_index,
    )
    return payload.file_id if payload is not None else None


def _thread_id_from_config(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) else None


async def materialize_attachment(
    *,
    store: AttachmentStore,
    extractor: ContentExtractor,
    file_id: str,
    owner_id: str,
    offset: int = 0,
    max_chars: int | None = None,
) -> str | list[dict[str, Any]]:
    """Shared "fetch a file as model-context content" helper. Used by
    both the `read_attachment` tool (when the model invokes it) AND
    `AttachmentPreloadMiddleware` (when the runtime fabricates a
    synthetic tool call for an attached file).

    Text pagination: returns at most `max_chars` characters starting
    at `offset`. A file that fits entirely in one page (offset 0) is
    returned as plain text with no framing; a sliced result carries a
    header (filename + char range) and a footer with the next offset
    so the model knows exactly how to continue. Images ignore both
    params — they're all-or-nothing content blocks.

    Public so both callers share one source of truth on MIME dispatch,
    pagination, and error formatting.
    """
    try:
        file = await store.get(file_id=file_id, owner_id=owner_id)
        data = await store.read_bytes(file_id=file_id, owner_id=owner_id)
    except AttachmentNotFoundError:
        return f"[tool error] file {file_id!r} not found."

    # Images → multimodal content block (the chat client feeds this to
    # the model as a vision-capable input).
    if file.mime_type.startswith("image/"):
        try:
            block = extractor.to_image_block(
                data=data, mime_type=file.mime_type
            )
        except UnsupportedContentError:
            return (
                f"[tool error] image MIME {file.mime_type!r} not "
                "renderable as a content block."
            )
        return [block]

    # Text-bearing files → extract + paginate. Extraction is sync +
    # CPU-bound (PDF parsing) by design — see the extractor's docstring
    # — so the async boundary here threads it off the event loop.
    try:
        text = await asyncio.to_thread(
            extractor.extract_text, data=data, mime_type=file.mime_type
        )
    except UnsupportedContentError:
        # Not an error — audio, video, archives, binaries… anything the
        # extractor can't turn into text. The turn must not break and
        # the model should still know the file EXISTS (name, type,
        # size) so it can talk about it, even though it can't read the
        # bytes. The user can preview/download via the attachment chip.
        return (
            f"[Attachment '{file.filename}' ({file.mime_type}, "
            f"{_format_bytes(file.size_bytes)}) — this file type can't "
            "be read as text by the agent. The file is stored and the "
            "user can preview or download it from their message.]"
        )
    text = text.strip()
    if not text:
        return (
            f"[tool note] {file.filename} contained no extractable "
            "text (likely an image-only PDF or empty file)."
        )
    return _paginate_text(
        text=text,
        filename=file.filename,
        file_id=file_id,
        offset=offset,
        max_chars=max_chars,
    )


def _paginate_text(
    *,
    text: str,
    filename: str,
    file_id: str,
    offset: int,
    max_chars: int | None,
) -> str:
    """Slice `text` into the requested page and frame it so the model
    can navigate: header says WHERE in the file this slice sits,
    footer says HOW to get the next page (or that the file ended)."""
    page = max_chars if max_chars and max_chars > 0 else _DEFAULT_PAGE_CHARS
    total = len(text)
    offset = max(0, offset)

    if offset >= total:
        return (
            f"[tool note] offset {offset:,} is beyond the end of "
            f"'{filename}' ({total:,} chars). Nothing to read."
        )

    # Whole file fits in one page from the start — return it bare, no
    # framing noise for the common small-file case.
    if offset == 0 and total <= page:
        return text

    end = min(offset + page, total)
    header = f"['{filename}' — chars {offset:,}–{end:,} of {total:,}]\n"
    body = text[offset:end]
    if end < total:
        footer = (
            f"\n\n[{total - end:,} chars remain. Call "
            f"read_attachment(file_id='{file_id}', offset={end}) "
            "to continue reading.]"
        )
    else:
        footer = "\n\n[end of file]"
    return header + body + footer


# ---- module helpers ----------------------------------------------------------


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _owner_id_from_config(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    owner_id = configurable.get("owner_id")
    return owner_id if isinstance(owner_id, str) else None
