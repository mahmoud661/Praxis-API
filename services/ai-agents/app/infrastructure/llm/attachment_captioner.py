"""
AttachmentCaptioner — the app's implementation of the react_agent
library's `CaptionModel` port.

The library decides WHEN a caption is needed (attachment compaction
stubs, the preload's OCR fallback for text-only agents) and WHAT to do
with it; this adapter decides HOW the model is called — LiteLLM proxy,
auth from env, request shape, timeouts. Swap the model provider and
only this file changes; the library never knows.

Both methods return `None` on any failure — the library degrades to
filename-based captions, never raises.
"""

from __future__ import annotations

import base64

import httpx

from ...domain.ports.logger import Logger
from ..config.env import Env

# Tight caption budget — these go into eviction stubs, where they're
# just a hint for the model, not the source of truth. The bytes are
# always recoverable via `read_attachment`.
_CAPTION_PROMPT = (
    "In ONE short sentence (max 15 words), describe what this is. "
    "No preamble, no markdown, just the description."
)


class AttachmentCaptioner:
    """Registered in the container as `"AttachmentCaptioner"`."""

    def __init__(self, env: Env, logger: Logger) -> None:
        self._env = env
        self._logger = logger

    async def caption_image(
        self, *, data: bytes, mime_type: str
    ) -> str | None:
        """Vision call via the LiteLLM proxy. Same auth/base-url as the
        chat client."""
        b64 = base64.b64encode(data).decode("ascii")
        body = {
            "model": self._env.litellm_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _CAPTION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 60,
        }
        return await self._chat_completion(body)

    async def caption_text(
        self, *, filename: str, preview: str
    ) -> str | None:
        body = {
            "model": self._env.litellm_model,
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
        return await self._chat_completion(body)

    async def _chat_completion(self, body: dict) -> str | None:
        """One-shot call to /chat/completions. Returns the assistant
        text or None on any failure (network, non-200, parse). Hard
        request timeout so a hung proxy doesn't stall compaction."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=5.0)
            ) as client:
                resp = await client.post(
                    f"{self._env.litellm_proxy_api_base.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": (
                            f"Bearer {self._env.litellm_proxy_api_key}"
                        ),
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
            self._logger.warning("caption.llm_call_failed", error=repr(exc))
            return None
        if not isinstance(text, str):
            return None
        text = text.strip().strip(".").strip()
        return text or None
