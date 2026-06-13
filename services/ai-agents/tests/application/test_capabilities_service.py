"""Unit tests for `CapabilitiesService`.

Exercises the composition logic — derived MIME types, attachment caps,
underlying-model augmentation. Doesn't hit any network or DB: the
registry runs on fixture agents and the LiteLLM client is mocked."""

from pathlib import Path

import pytest

from app.application.services.agentic.agent_registry import AgentRegistry
from app.application.services.capabilities_service import CapabilitiesService
from app.infrastructure.llm.litellm_client import ModelInfo


class _FakeLogger:
    def info(self, *a: object, **kw: object) -> None: pass
    def warning(self, *a: object, **kw: object) -> None: pass
    def error(self, *a: object, **kw: object) -> None: pass
    def debug(self, *a: object, **kw: object) -> None: pass


_FIXTURE_FOLDER = Path(__file__).parent / "fixture_agents"
_FIXTURE_PACKAGE = "tests.application.fixture_agents"


class _MockLiteLLM:
    def __init__(self, models: dict[str, ModelInfo]) -> None:
        self._models = models

    async def list_models(self, *, force_refresh: bool = False) -> dict[str, ModelInfo]:
        del force_refresh
        return self._models


def _model(name: str) -> ModelInfo:
    return ModelInfo(
        model_name=name,
        provider="anthropic",
        max_input_tokens=200000,
        max_output_tokens=64000,
        supports_vision=True,
        supports_pdf_input=True,
        supports_audio_input=False,
        supports_function_calling=True,
        supports_tool_choice=True,
        supports_response_schema=True,
        supports_prompt_caching=False,
        supports_system_messages=True,
        input_cost_per_token=0.000003,   # $3 per 1M
        output_cost_per_token=0.000015,  # $15 per 1M
    )


def _service() -> tuple[CapabilitiesService, AgentRegistry]:
    registry = AgentRegistry(
        agents_folder=_FIXTURE_FOLDER,
        logger=_FakeLogger(),
        package=_FIXTURE_PACKAGE,
    )
    registry.discover()
    svc = CapabilitiesService(
        agent_registry=registry,
        litellm_client=_MockLiteLLM({"test-model": _model("test-model")}),  # type: ignore[arg-type]
        logger=_FakeLogger(),
    )
    return svc, registry


@pytest.mark.asyncio
async def test_list_returns_every_agent_in_registry():
    svc, _ = _service()
    view = await svc.list_capabilities(user_id="u1")
    ids = [a.id for a in view.agents]
    assert set(ids) == {"alpha", "bravo", "charlie"}  # charlie = package layout


@pytest.mark.asyncio
async def test_text_only_agent_has_no_mime_types_or_attachment_cap():
    svc, _ = _service()
    view = await svc.list_capabilities(user_id="u1")
    bravo = next(a for a in view.agents if a.id == "bravo")  # accepts ["text"]
    # Text-only agents don't expose a file picker — empty mime list +
    # zero attachment cap so the frontend hides the paperclip.
    assert bravo.accepts.mime_types == []
    assert bravo.limits.max_attachment_bytes == 0
    assert bravo.limits.max_attachments_per_turn == 0


@pytest.mark.asyncio
async def test_multimodal_agent_derives_mime_allowlist():
    svc, _ = _service()
    view = await svc.list_capabilities(user_id="u1")
    alpha = next(a for a in view.agents if a.id == "alpha")  # accepts ["text", "image"]

    # Image MIME types included.
    assert "image/jpeg" in alpha.accepts.mime_types
    assert "image/png" in alpha.accepts.mime_types
    # PDF not declared → not in the list.
    assert "application/pdf" not in alpha.accepts.mime_types
    # Multimodal agents ALSO accept text uploads (so users can drag a
    # .md file onto the composer without surprise rejection).
    assert "text/markdown" in alpha.accepts.mime_types
    # Default attachment cap applied.
    assert alpha.limits.max_attachment_bytes == 32 * 1024 * 1024
    assert alpha.limits.max_attachments_per_turn == 8


@pytest.mark.asyncio
async def test_underlying_block_augmented_from_litellm():
    svc, _ = _service()
    view = await svc.list_capabilities(user_id="u1")
    alpha = next(a for a in view.agents if a.id == "alpha")

    assert alpha.underlying.model_id == "test-model"
    assert alpha.underlying.vendor == "anthropic"
    assert alpha.underlying.context_window == 200000
    assert alpha.underlying.pricing is not None
    # 0.000003 USD/token × 1_000_000 = 3.0 per 1M.
    assert alpha.underlying.pricing.input_per_1m_usd == 3.0
    assert alpha.underlying.pricing.output_per_1m_usd == 15.0


@pytest.mark.asyncio
async def test_unknown_underlying_model_returns_minimal_block():
    # Registry is happy at discover time (validation runs separately);
    # at request time, missing model info doesn't 500 — the response
    # carries an "unknown" underlying block so the frontend stays alive.
    registry = AgentRegistry(
        agents_folder=_FIXTURE_FOLDER,
        logger=_FakeLogger(),
        package=_FIXTURE_PACKAGE,
    )
    registry.discover()
    svc = CapabilitiesService(
        agent_registry=registry,
        litellm_client=_MockLiteLLM({}),  # empty catalog  # type: ignore[arg-type]
        logger=_FakeLogger(),
    )

    view = await svc.list_capabilities(user_id="u1")
    alpha = next(a for a in view.agents if a.id == "alpha")
    assert alpha.underlying.vendor == "unknown"
    assert alpha.underlying.context_window == 0
    assert alpha.underlying.pricing is None


@pytest.mark.asyncio
async def test_schema_version_present():
    svc, _ = _service()
    view = await svc.list_capabilities(user_id="u1")
    # Just assert it exists + non-empty — the value bumps over time.
    assert view.schema_version
    assert len(view.schema_version) >= 6


@pytest.mark.asyncio
async def test_default_agent_id_from_registry():
    svc, registry = _service()
    view = await svc.list_capabilities(user_id="u1")
    assert view.default_agent_id == registry.default_id()
