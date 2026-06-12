"""Unit tests for `AgentRegistry` discovery + validation.

Uses fixture agents in `tests/application/fixture_agents/` so the tests
don't depend on the real `app.application.services.agentic.agents`
package (which pulls in LangGraph + the LiteLLM proxy)."""

from pathlib import Path

import pytest

from app.application.services.agentic.agent_registry import (
    AgentRegistry,
    AgentRegistryError,
)
from app.infrastructure.llm.litellm_client import ModelInfo


class _FakeLogger:
    def info(self, *a: object, **kw: object) -> None: ...
    def warning(self, *a: object, **kw: object) -> None: ...
    def error(self, *a: object, **kw: object) -> None: ...
    def debug(self, *a: object, **kw: object) -> None: ...


_FIXTURE_FOLDER = Path(__file__).parent / "fixture_agents"
_FIXTURE_PACKAGE = "tests.application.fixture_agents"


def _make_registry() -> AgentRegistry:
    return AgentRegistry(
        agents_folder=_FIXTURE_FOLDER,
        logger=_FakeLogger(),
        package=_FIXTURE_PACKAGE,
    )


def test_discover_finds_every_base_agent_subclass():
    registry = _make_registry()
    registry.discover()
    ids = {s.id for s in registry.specs()}
    assert ids == {"alpha", "bravo", "charlie"}  # charlie = package layout


def test_discover_is_idempotent():
    registry = _make_registry()
    registry.discover()
    registry.discover()  # second call no-ops
    assert len(registry.specs()) == 3


def test_default_id_prefers_general_else_lex_first():
    # No "general" in the fixture — falls back to alphabetic first.
    registry = _make_registry()
    registry.discover()
    assert registry.default_id() == "alpha"


def test_specs_are_sorted_for_deterministic_output():
    registry = _make_registry()
    registry.discover()
    ids = [s.id for s in registry.specs()]
    assert ids == sorted(ids)


def test_get_returns_instance_or_none():
    registry = _make_registry()
    registry.discover()
    assert registry.get("alpha") is not None
    assert registry.get("alpha").spec.id == "alpha"
    assert registry.get("does-not-exist") is None


def test_discover_in_empty_folder_raises(tmp_path: Path):
    # An empty fixture folder (no .py files except __init__) must fail
    # loudly rather than silently boot a service with zero agents.
    (tmp_path / "__init__.py").write_text("")
    registry = AgentRegistry(
        agents_folder=tmp_path,
        logger=_FakeLogger(),
        package="nonexistent.package",
    )
    with pytest.raises(AgentRegistryError, match="no BaseAgent subclasses"):
        registry.discover()


class _MockLiteLLM:
    """Stub `LiteLLMClient` that returns whatever model catalog the
    test wires up. Avoids spinning a real httpx mock for cross-check
    tests that don't care about wire details."""

    def __init__(self, models: dict[str, ModelInfo]) -> None:
        self._models = models

    async def list_models(self, *, force_refresh: bool = False) -> dict[str, ModelInfo]:
        del force_refresh
        return self._models


def _make_model(name: str, *, vision: bool = False, pdf: bool = False) -> ModelInfo:
    return ModelInfo(
        model_name=name,
        provider="test",
        max_input_tokens=200000,
        max_output_tokens=64000,
        supports_vision=vision,
        supports_pdf_input=pdf,
        supports_audio_input=False,
        supports_function_calling=True,
        supports_tool_choice=True,
        supports_response_schema=False,
        supports_prompt_caching=False,
        supports_system_messages=True,
        input_cost_per_token=0.0,
        output_cost_per_token=0.0,
    )


@pytest.mark.asyncio
async def test_validate_against_passes_when_modalities_match():
    registry = _make_registry()
    registry.discover()

    # Alpha declares [text, image]; Bravo declares [text]. Both run on
    # "test-model" — give it both vision+pdf capability so it's a
    # superset of both.
    litellm = _MockLiteLLM({"test-model": _make_model("test-model", vision=True, pdf=True)})
    await registry.validate_against(litellm)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_validate_against_fails_when_agent_modality_unsupported():
    registry = _make_registry()
    registry.discover()

    # Model has no vision support → alpha's image declaration fails.
    litellm = _MockLiteLLM({"test-model": _make_model("test-model", vision=False)})
    with pytest.raises(AgentRegistryError) as exc:
        await registry.validate_against(litellm)  # type: ignore[arg-type]
    assert "alpha" in str(exc.value)
    assert "image" in str(exc.value)


@pytest.mark.asyncio
async def test_validate_against_fails_when_underlying_model_missing():
    registry = _make_registry()
    registry.discover()

    litellm = _MockLiteLLM({})  # no models at all
    with pytest.raises(AgentRegistryError) as exc:
        await registry.validate_against(litellm)  # type: ignore[arg-type]
    msg = str(exc.value)
    # Both agents fail — error lists both.
    assert "alpha" in msg
    assert "bravo" in msg
    assert "test-model" in msg


@pytest.mark.asyncio
async def test_validate_before_discover_raises():
    registry = _make_registry()
    with pytest.raises(AgentRegistryError, match="before discover"):
        await registry.validate_against(_MockLiteLLM({}))  # type: ignore[arg-type]
