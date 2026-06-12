"""
TitleGenerator — small wrapper around the LLM that produces a short,
conversation-style title from the first user message of a thread. Same
mechanism ChatGPT uses to auto-name conversations in its sidebar.

The model is the same LiteLLM-proxied chat model the main agent uses
(low cost-of-failure: it's just text-in, text-out — no tools, no graph,
short max-tokens to keep latency in the tens of ms).

Returns `None` on any failure (network blip, model refusal, blank
output) so the caller can leave the existing title alone — auto-titling
is a quality-of-life feature, never load-bearing.
"""

from __future__ import annotations

from typing import AsyncIterator

from langchain_openai import ChatOpenAI

from ...domain.ports.logger import Logger
from ..config.env import Env


_PROMPT = (
    "Generate a concise conversation title for a chat. "
    "Constraints: 5 words or fewer, no quotes, no trailing punctuation, "
    "no markdown, plain text only. The title should capture the main "
    "topic of the user's first message at a glance.\n\n"
    "Return ONLY the title — no explanation, no preamble.\n\n"
    "User message:\n{user_message}"
)


class TitleGenerator:
    """Auto-DI registered under token ``\"TitleGenerator\"``."""

    def __init__(self, env: Env, logger: Logger) -> None:
        self._env = env
        self._logger = logger
        self._model: ChatOpenAI | None = None

    def _client(self) -> ChatOpenAI:
        if self._model is None:
            # Same LiteLLM-compatible config as the agents. Cap tokens
            # tight — five words won't exceed ~20 tokens, and the model
            # occasionally tries to over-explain when given more room.
            self._model = ChatOpenAI(
                model=self._env.litellm_model,
                api_key=self._env.litellm_proxy_api_key,
                base_url=self._env.litellm_proxy_api_base,
                temperature=0.3,
                max_tokens=20,
            )
        return self._model

    async def stream(self, *, user_message: str) -> AsyncIterator[str]:
        """Stream title tokens as they're generated.

        Yields the cumulative SANITIZED title after each LLM chunk —
        callers can publish each yield straight to the frontend so the
        sidebar's title types out live.

        Yields nothing on empty input or LLM failure (caller can treat
        an empty stream as "no title produced").
        """
        text = (user_message or "").strip()
        if not text:
            return
        prompt = _PROMPT.format(user_message=text[:500])
        accumulated = ""
        last_emitted = ""
        try:
            async for chunk in self._client().astream(prompt):
                content = getattr(chunk, "content", None)
                if not content:
                    continue
                # Flatten list-of-blocks defensively (Anthropic does this).
                if not isinstance(content, str):
                    content = "".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in content
                    )
                if not content:
                    continue
                accumulated += content
                sanitized = sanitize_title(accumulated)
                if sanitized and sanitized != last_emitted:
                    last_emitted = sanitized
                    yield sanitized
        except Exception as err:  # noqa: BLE001
            self._logger.warning("title_gen.stream_failed", error=str(err))
            return


def sanitize_title(raw: str) -> str:
    """Clean up an in-flight or final title — first line, strip wrapping
    quotes, drop trailing punctuation, hard-cap length so a runaway
    model can't blow out the sidebar."""
    if not raw:
        return ""
    title = raw.split("\n")[0].strip()
    # Strip up to one leading + trailing quote/backtick of each kind.
    for ch in ('"', "'", "`"):
        if title.startswith(ch):
            title = title[1:]
        if title.endswith(ch):
            title = title[:-1]
    title = title.rstrip(".").strip()
    if len(title) > 60:
        cut = title[:60].rsplit(" ", 1)
        title = cut[0] if cut[0] else title[:60]
    return title
