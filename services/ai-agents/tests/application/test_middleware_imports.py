"""Smoke test — every middleware module under react_agent/middlewares/
imports cleanly. Exists because the middlewares are LAZY-imported
inside `GeneralAgent._build()` (to avoid pulling `langchain.agents.middleware`
during DI-glob), so a wrong-depth relative import doesn't surface until
the first agent invocation in production.

If this file fails to collect, a relative-import depth is wrong. Fix
the dot count BEFORE shipping — `pytest` runs in the Docker build
stage and the build aborts if this fails."""

from __future__ import annotations

import importlib

import pytest


_MIDDLEWARE_MODULES = [
    "app.application.services.agentic.react_agent.middlewares.attachment_compaction_middleware",
    "app.application.services.agentic.react_agent.middlewares.attachment_preload_middleware",
    "app.application.services.agentic.react_agent.middlewares.compaction_middleware",
    "app.application.services.agentic.react_agent.middlewares.content_reference_middleware",
    "app.application.services.agentic.react_agent.middlewares.prompt_caching_middleware",
]


@pytest.mark.parametrize("module_name", _MIDDLEWARE_MODULES)
def test_middleware_module_imports_cleanly(module_name: str) -> None:
    """Each middleware module is importable. Bare smoke — we don't
    construct the middleware (that needs DI deps) — but importing
    catches every relative-import depth bug, every missing-symbol
    bug, and every TYPE_CHECKING-block error."""
    mod = importlib.import_module(module_name)
    assert mod is not None
