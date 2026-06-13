"""
Lazy captions for evicted attachments.

When `AttachmentCompactionMiddleware` is about to replace an attachment
with a stub for the first time, it calls `generate_attachment_caption`.
Result is one short sentence ("login form screenshot with two input
fields"). The caller persists it via `AttachmentStore.set_caption` so
subsequent evictions reuse the cached value — caption generation is
paid at most once per file.

The actual model calls go through the `CaptionModel` PORT — this
module owns the orchestration (which path for which MIME, fallbacks),
the host owns the transport (which model, which proxy, auth).

Behavior:
  - image MIME → `captioner.caption_image`
  - text-bearing MIME → first ~2000 chars via `captioner.caption_text`
  - unsupported MIME → filename-based caption (no model call)
  - any failure → filename-based caption + log

Callers never handle exceptions — failure paths return a sensible
string. Compaction continues; stubs just lose the caption flourish.
"""

from __future__ import annotations

import asyncio

from .ports import (
    AttachmentStore,
    CaptionModel,
    ContentExtractor,
    LoggerLike,
)

_TEXT_PREVIEW_CHARS = 2000


async def generate_attachment_caption(
    *,
    store: AttachmentStore,
    extractor: ContentExtractor,
    captioner: CaptionModel,
    logger: LoggerLike,
    file_id: str,
    owner_id: str,
) -> str:
    """Build a short caption for one file. Always returns a string,
    even on failure (degrades to a filename-based caption)."""
    try:
        view = await store.get(file_id=file_id, owner_id=owner_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "caption.file_lookup_failed", file_id=file_id, error=repr(exc)
        )
        return _filename_fallback(filename=file_id, mime_type="unknown")

    if view.mime_type.startswith("image/"):
        try:
            data = await store.read_bytes(file_id=file_id, owner_id=owner_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "caption.image_bytes_failed",
                file_id=file_id,
                error=repr(exc),
            )
            return _filename_fallback(
                filename=view.filename, mime_type=view.mime_type
            )
        caption = await captioner.caption_image(
            data=data, mime_type=view.mime_type
        )
        return caption or _filename_fallback(
            filename=view.filename, mime_type=view.mime_type
        )

    # Text-bearing path — extract a preview slice, ask the model to
    # summarise. Anything the extractor can't handle (audio, video)
    # falls back to filename-only.
    text = await _safe_extract_text(
        store=store,
        extractor=extractor,
        mime_type=view.mime_type,
        file_id=file_id,
        owner_id=owner_id,
        logger=logger,
    )
    if not text:
        return _filename_fallback(
            filename=view.filename, mime_type=view.mime_type
        )
    caption = await captioner.caption_text(
        filename=view.filename, preview=text[:_TEXT_PREVIEW_CHARS]
    )
    return caption or _filename_fallback(
        filename=view.filename, mime_type=view.mime_type
    )


# ---- module helpers ----------------------------------------------------------


async def _safe_extract_text(
    *,
    store: AttachmentStore,
    extractor: ContentExtractor,
    mime_type: str,
    file_id: str,
    owner_id: str,
    logger: LoggerLike,
) -> str:
    """Read bytes + extract text. Returns "" for unsupported MIMEs
    (audio, video, octet-stream) or any failure — caller treats "" as
    "no caption possible from content"."""
    if not extractor.supports(mime_type=mime_type):
        return ""
    try:
        data = await store.read_bytes(file_id=file_id, owner_id=owner_id)
        # Sync, CPU-bound extraction (PDF parsing) — keep it off the
        # event loop; the extractor is intentionally not async.
        return await asyncio.to_thread(
            extractor.extract_text, data=data, mime_type=mime_type
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "caption.text_extract_failed", file_id=file_id, error=repr(exc)
        )
        return ""


def _filename_fallback(*, filename: str, mime_type: str) -> str:
    """Last-resort caption when the model path fails. Better than the
    empty string — the model still gets the filename + type."""
    return f"a {mime_type} file '{filename}'"
