"""
LiteLLMClient — minimal HTTP client for the LiteLLM proxy admin API.

Currently uses one endpoint: `GET /model/info`. Returns the model
catalog the proxy is configured with, including every `supports_*` flag,
token limits, and per-token costs. That's the source of truth for
"what does each underlying model in our config.yaml support".

Used by:

  1. AgentRegistry — at boot, validates each agent's `accepts_modalities`
     is achievable on its `underlying_model`.

  2. CapabilitiesService — composes the `underlying` block of each
     agent's capability view (vendor, context_window, pricing).

Cached for 5 minutes per process. LiteLLM's config doesn't churn during
a session; one fresh fetch per chat session is more than enough.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from ...domain.ports.logger import Logger


# What we extract from each `/model/info` row. LiteLLM returns a much
# wider shape but we only need these fields and don't want callers
# coupled to the upstream wire format.
@dataclass(frozen=True, slots=True)
class ModelInfo:
    model_name: str  # the `litellm` key — what agents reference
    provider: str  # "anthropic" | "openai" | "gemini" | ...
    max_input_tokens: int
    max_output_tokens: int

    supports_vision: bool
    supports_pdf_input: bool
    supports_audio_input: bool
    supports_function_calling: bool
    supports_tool_choice: bool
    supports_response_schema: bool
    supports_prompt_caching: bool
    supports_system_messages: bool

    input_cost_per_token: float
    output_cost_per_token: float

    @property
    def supported_modalities(self) -> set[str]:
        """The set an agent's `accepts_modalities` must be a subset of."""
        out = {"text"}
        if self.supports_vision:
            out.add("image")
        if self.supports_pdf_input:
            out.add("pdf")
        if self.supports_audio_input:
            out.add("audio")
        return out


_CACHE_TTL_SECONDS = 300.0


class LiteLLMClient:
    """Thin wrapper over `httpx.AsyncClient` targeting the LiteLLM proxy.

    Container-registered as `"LiteLLMClient"`. The base URL + master key
    come from `Env` (`litellm_proxy_api_base`, `litellm_master_key`);
    the client constructs its own httpx instance so it isn't tangled
    with any other HTTP traffic.
    """

    def __init__(
        self,
        base_url: str,
        master_key: str | None,
        logger: Logger,
        *,
        # Injectable for tests — pass an httpx.MockTransport in unit
        # tests so we don't need a live LiteLLM proxy. Production code
        # always uses the default httpx client.
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._master_key = master_key
        self._logger = logger
        # Cache layout: (fetched_at_monotonic, models_by_name).
        self._cache: tuple[float, dict[str, ModelInfo]] | None = None
        # Lazily-built httpx client. We can't construct it at import time
        # because there's no running event loop yet.
        self._owned_client = client is None
        self._client = client

    async def aclose(self) -> None:
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_models(self, *, force_refresh: bool = False) -> dict[str, ModelInfo]:
        """Return the model catalog keyed by `model_name`. Cached 5 min.

        `force_refresh=True` bypasses the cache — used by the agent
        registry's boot-time validation so we don't validate against
        a stale snapshot from a previous process restart's cache."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._cache is not None
            and (now - self._cache[0]) < _CACHE_TTL_SECONDS
        ):
            return self._cache[1]

        models = await self._fetch_model_info()
        self._cache = (now, models)
        return models

    async def _fetch_model_info(self) -> dict[str, ModelInfo]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)

        headers: dict[str, str] = {}
        if self._master_key:
            headers["Authorization"] = f"Bearer {self._master_key}"

        try:
            resp = await self._client.get(
                f"{self._base_url}/model/info", headers=headers
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            # Don't crash the service if LiteLLM is briefly unreachable —
            # the previous cache is gone but callers can degrade.
            self._logger.warning(
                "litellm.model_info.fetch_failed",
                error=str(exc),
                base_url=self._base_url,
            )
            raise

        payload = resp.json()
        rows = payload.get("data") or []
        out: dict[str, ModelInfo] = {}
        for row in rows:
            info = _parse_row(row)
            if info is not None:
                out[info.model_name] = info
        self._logger.info(
            "litellm.model_info.loaded", count=len(out), base_url=self._base_url
        )
        return out


def _parse_row(row: object) -> ModelInfo | None:
    """Tolerantly parse one `/model/info` entry. Unknown / missing
    fields take sane defaults; a row without a `model_name` is skipped."""
    if not isinstance(row, dict):
        return None
    name = row.get("model_name")
    if not isinstance(name, str) or not name:
        return None
    info = row.get("model_info") or {}
    if not isinstance(info, dict):
        info = {}
    return ModelInfo(
        model_name=name,
        provider=str(info.get("litellm_provider") or "unknown"),
        max_input_tokens=int(info.get("max_input_tokens") or 0),
        max_output_tokens=int(info.get("max_output_tokens") or 0),
        supports_vision=bool(info.get("supports_vision")),
        supports_pdf_input=bool(info.get("supports_pdf_input")),
        supports_audio_input=bool(info.get("supports_audio_input")),
        supports_function_calling=bool(info.get("supports_function_calling")),
        supports_tool_choice=bool(info.get("supports_tool_choice")),
        supports_response_schema=bool(info.get("supports_response_schema")),
        supports_prompt_caching=bool(info.get("supports_prompt_caching")),
        # System messages are universally supported in modern chat models;
        # default true so an absent field in older LiteLLM versions
        # doesn't make every agent claim it can't use system prompts.
        supports_system_messages=bool(info.get("supports_system_messages", True)),
        input_cost_per_token=float(info.get("input_cost_per_token") or 0.0),
        output_cost_per_token=float(info.get("output_cost_per_token") or 0.0),
    )
