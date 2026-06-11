"""
Lazy LLM-generated captions for evicted attachments.

When `AttachmentCompactionMiddleware` is about to replace an
attachment with a stub for the first time, it calls
`generate_attachment_caption()` here. Result is one short sentence
("login form screenshot with two input fields", "PDF table of Q3
revenue by region"). The caller persists it on the file's metadata
via `IFilesService.set_caption` so subsequent evictions just reuse
the cached value — caption generation is paid at most once per file.

Why this lives in `agentic/` not in `infrastructure/` even though it
talks to the LLM: it's an agent-runtime concern (only called from the
compaction middleware, only meaningful inside an active run). Keeping
it next to the middleware that uses it makes the dependency obvious.

Underscore-prefixed module name so the DI auto-discovery globber
skips it.

Behavior:
  - image MIME → vision call ("describe in 12 words or fewer")
  - text-bearing MIME → first ~2000 chars summarized
  - unsupported MIME → falls back to filename-based caption (no LLM)
  - LLM failure → falls back to filename-based caption + logs

Caller never has to handle exceptions — failure paths return a
sensible string. Compaction continues; eviction stubs just lose the
caption flourish.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ....domain.IServices.i_files_service import IFilesService
    from ....domain.ports.document_extractor import IDocumentExtractor
    from ....domain.ports.logger import Logger
    from ....infrastructure.config.env import Env


# Tight caption budget — these go into eviction stubs, where they're
# just a hint for the model, not the source of truth. The bytes are
# always recoverable via `read_attachment`.
_CAPTION_PROMPT = (
    "In ONE short sentence (max 15 words), describe what this is. "
    "No preamble, no markdown, just the description."
)
_TEXT_PREVIEW_CHARS = 2000


async def generate_attachment_caption(
    *,
    files: "IFilesService",
    extractor: "IDocumentExtractor",
    env: "Env",
    logger: "Logger",
    file_id: str,
    owner_id: str,
) -> str:
    """Build a short caption for one file. Always returns a string,
    even on failure (degrades to a filename-based caption)."""
    try:
        view = await files.get(file_id=file_id, owner_id=owner_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "caption.file_lookup_failed", file_id=file_id, error=repr(exc)
        )
        return _filename_fallback(filename=file_id, mime_type="unknown")

    if view.mime_type.startswith("image/"):
        try:
            data = await files.read_bytes(file_id=file_id, owner_id=owner_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "caption.image_bytes_failed",
                file_id=file_id,
                error=repr(exc),
            )
            return _filename_fallback(
                filename=view.filename, mime_type=view.mime_type
            )
        caption = await _caption_image(
            data=data, mime_type=view.mime_type, env=env, logger=logger
        )
        return caption or _filename_fallback(
            filename=view.filename, mime_type=view.mime_type
        )

    # Text-bearing path — extract a preview slice, ask the model to
    # summarise. Anything the extractor can't handle (audio, video)
    # falls back to filename-only.
    try:
        text = await _safe_extract_text(
            extractor=extractor, data=None, mime_type=view.mime_type,
            files=files, file_id=file_id, owner_id=owner_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "caption.text_extract_failed", file_id=file_id, error=repr(exc)
        )
        text = ""
    if not text:
        return _filename_fallback(
            filename=view.filename, mime_type=view.mime_type
        )
    preview = text[:_TEXT_PREVIEW_CHARS]
    caption = await _caption_text(
        filename=view.filename, preview=preview, env=env, logger=logger
    )
    return caption or _filename_fallback(
        filename=view.filename, mime_type=view.mime_type
    )


# ---- module helpers ----------------------------------------------------------


async def _safe_extract_text(
    *,
    extractor: "IDocumentExtractor",
    data: bytes | None,
    mime_type: str,
    files: "IFilesService",
    file_id: str,
    owner_id: str,
) -> str:
    """Read bytes + extract text. Returns "" for unsupported MIMEs
    (audio, video, octet-stream) rather than raising — caller treats
    "" as "no caption possible from content"."""
    if not extractor.supports(mime_type=mime_type):
        return ""
    bytes_ = data
    if bytes_ is None:
        bytes_ = await files.read_bytes(file_id=file_id, owner_id=owner_id)
    try:
        return extractor.extract_text(data=bytes_, mime_type=mime_type)
    except Exception:  # noqa: BLE001
        return ""


async def _caption_image(
    *,
    data: bytes,
    mime_type: str,
    env: "Env",
    logger: "Logger",
) -> str | None:
    """Vision call via the LiteLLM proxy. Same auth/base-url as the
    chat client. Returns None on any error so the caller falls back
    to the filename-based caption."""
    b64 = base64.b64encode(data).decode("ascii")
    body = {
        "model": env.litellm_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _CAPTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 60,
    }
    return await _chat_completion(body=body, env=env, logger=logger)


async def _caption_text(
    *,
    filename: str,
    preview: str,
    env: "Env",
    logger: "Logger",
) -> str | None:
    body = {
        "model": env.litellm_model,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"{_CAPTION_PROMPT}\n\n"
                    f"File: {filename}\n"
                    f"--- begin content ---\n{preview}\n--- end content ---"
                ),
            }
        ],
        "max_tokens": 60,
    }
    return await _chat_completion(body=body, env=env, logger=logger)


async def _chat_completion(
    *, body: dict, env: "Env", logger: "Logger"
) -> str | None:
    """One-shot call to /chat/completions. Returns the assistant text
    or None on any failure (network, non-200, parse). Hard-limited
    request timeout so a hung proxy doesn't stall compaction."""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0)
        ) as client:
            resp = await client.post(
                f"{env.litellm_proxy_api_base.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {env.litellm_proxy_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        resp.raise_for_status()
        payload = resp.json()
        text = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("caption.llm_call_failed", error=repr(exc))
        return None
    if not isinstance(text, str):
        return None
    text = text.strip().strip(".").strip()
    return text or None


def _filename_fallback(*, filename: str, mime_type: str) -> str:
    """Last-resort caption when the LLM path fails. Better than the
    empty string — the model still gets the filename + type."""
    return f"a {mime_type} file '{filename}'"
