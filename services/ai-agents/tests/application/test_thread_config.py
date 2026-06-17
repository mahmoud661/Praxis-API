"""Tests for `ThreadsService.update_config` validation against the
agent registry.

The validation logic doesn't touch the repo or the agentic store, so
the test instantiates the service with stubs for everything except the
registry — keeping the surface small enough to exercise every branch
of `_validate_config` directly via a private call."""

from pathlib import Path

import pytest

from app.application.services._errors import InvalidThreadConfigError
from app.application.services.agentic.agent_registry import AgentRegistry
from app.application.services.threads_service import ThreadsService
from app.domain.dtos.thread_dto import ThreadConfigView


class _Stub:
    """Generic permissive stub for the constructor deps the validator
    doesn't touch (repo, agentic_store, title_generator, redis, logger)."""

    def __getattr__(self, _name: str):
        async def _async(*a: object, **kw: object) -> None:
            return None

        return _async

    def info(self, *a: object, **kw: object) -> None: pass
    def warning(self, *a: object, **kw: object) -> None: pass
    def error(self, *a: object, **kw: object) -> None: pass
    def debug(self, *a: object, **kw: object) -> None: pass


_FIXTURE_FOLDER = Path(__file__).parent / "fixture_agents"
_FIXTURE_PACKAGE = "tests.application.fixture_agents"


def _registry() -> AgentRegistry:
    reg = AgentRegistry(
        agents_folder=_FIXTURE_FOLDER,
        logger=_Stub(),
        package=_FIXTURE_PACKAGE,
    )
    reg.discover()
    return reg


def _service(registry: AgentRegistry) -> ThreadsService:
    stub = _Stub()
    return ThreadsService(
        thread_repo=stub,  # type: ignore[arg-type]
        agentic_store=stub,  # type: ignore[arg-type]
        title_generator=stub,  # type: ignore[arg-type]
        redis=stub,  # type: ignore[arg-type]
        logger=stub,  # type: ignore[arg-type]
        agent_registry=registry,
        event_publisher=stub,  # type: ignore[arg-type]
    )


def test_validate_accepts_empty_config():
    """No agent_id, no overrides — pure default. Valid."""
    svc = _service(_registry())
    svc._validate_config(ThreadConfigView())  # no raise


def test_validate_accepts_known_agent_id():
    svc = _service(_registry())
    svc._validate_config(ThreadConfigView(agent_id="alpha"))


def test_validate_rejects_unknown_agent_id():
    svc = _service(_registry())
    with pytest.raises(InvalidThreadConfigError, match="unknown agent_id"):
        svc._validate_config(ThreadConfigView(agent_id="ghost"))


def test_validate_overrides_against_explicit_agent_id():
    """The fixture agents declare no tools, so any override should fail."""
    svc = _service(_registry())
    with pytest.raises(InvalidThreadConfigError, match="has no tool"):
        svc._validate_config(
            ThreadConfigView(
                agent_id="alpha",
                tool_overrides={"web_search": True},
            )
        )


def test_validate_overrides_against_default_agent_when_unset():
    """When `agent_id` is unset, overrides validate against the default
    agent's tool list — same shape, just resolved differently."""
    svc = _service(_registry())
    with pytest.raises(InvalidThreadConfigError, match="has no tool"):
        svc._validate_config(
            ThreadConfigView(tool_overrides={"web_search": True})
        )
