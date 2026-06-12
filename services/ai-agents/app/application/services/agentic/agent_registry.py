"""
AgentRegistry — discovers `BaseAgent` subclasses + serves them by spec id.

Discovery scans the agents folder for AGENT PACKAGES — one folder per
agent, each exposing its `BaseAgent` subclass in an `agent.py` module:

    agents/
      general/
        agent.py       ← discovered here
        graph.py, sections.py, prompts/, tools/, middlewares/

Bare `*.py` modules directly under `agents/` are still discovered too
(the original flat layout), so a quick prototype agent doesn't need
the full folder ceremony. Mirrors the auto-mount pattern
`mount_routes()` uses for HTTP routes.

Two contracts:

  - `get(spec_id)` — return a built agent or None. The runner calls this
    with `thread.config.agent_id` to pick the graph to execute.

  - `specs()` — return every public spec. The capabilities service
    composes the `/v1/capabilities` response from these.

Boot validation: after discovery, the registry asks LiteLLM for the
model catalog and verifies that each agent's `accepts_modalities` is a
subset of `underlying_model.supported_modalities`. A mismatch is FATAL —
it would surface as a runtime error the first time a user attached an
image to an agent whose underlying model can't see it. Better to refuse
to boot.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Callable

from ....domain.ports.logger import Logger
from ....infrastructure.llm.litellm_client import LiteLLMClient
from .agent_spec import AgentSpec
from .base_agent import BaseAgent


class AgentRegistryError(RuntimeError):
    """Raised when registry discovery or validation fails. The service
    intentionally crashes — a half-loaded catalog is worse than no
    service at all."""


_DEFAULT_AGENTS_PACKAGE = "app.application.services.agentic.agents"


class AgentRegistry:
    """Container-registered as `"AgentRegistry"`.

    Production wires this with the platform's agents folder + package;
    tests can point both at fixture folders to exercise the discovery
    logic in isolation.
    """

    def __init__(
        self,
        agents_folder: Path,
        logger: Logger,
        constructor: Callable[[type[BaseAgent]], BaseAgent] | None = None,
        package: str = _DEFAULT_AGENTS_PACKAGE,
    ) -> None:
        """`constructor` is the callable that instantiates a discovered
        subclass — typically `Container.construct`, which resolves the
        subclass's `__init__` annotations from the container. Falls back
        to no-arg construction for tests that don't need DI.

        `package` is the Python import path the discovery loop joins
        each `*.py` filename to. Defaults to the platform agents
        package; override in tests to load fixture agents."""
        self._folder = agents_folder
        self._logger = logger
        self._package = package
        self._constructor: Callable[[type[BaseAgent]], BaseAgent] = (
            constructor or (lambda cls: cls())
        )
        self._agents: dict[str, BaseAgent] = {}
        self._discovered = False

    def discover(self) -> None:
        """Scan the agents folder and instantiate one of each subclass.
        Idempotent; second call is a no-op.

        Two layouts per entry, both supported:
          - PACKAGE: a folder with `__init__.py` + `agent.py` — the
            convention for real agents (tools/, prompts/, sections.py
            live next to the class). We import `<pkg>.<folder>.agent`.
          - MODULE: a bare `<name>.py` — the original flat layout,
            still fine for prototypes.

        Instantiation is cheap (constructor doesn't compile the graph —
        that's deferred to `BaseAgent.get()` on first use)."""
        if self._discovered:
            return
        for mod_name, origin in self._agent_modules():
            module = importlib.import_module(mod_name)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Only pick classes DEFINED in this module — skip
                # `BaseAgent` itself when it's imported for the type
                # annotation.
                if obj.__module__ != module.__name__:
                    continue
                if obj is BaseAgent or not issubclass(obj, BaseAgent):
                    continue
                if name.startswith("_"):
                    continue
                instance = self._constructor(obj)
                if not isinstance(instance, BaseAgent):
                    raise AgentRegistryError(
                        f"constructor for {name} did not return a BaseAgent"
                    )
                spec_id = instance.spec.id
                if spec_id in self._agents:
                    raise AgentRegistryError(
                        f"duplicate agent spec id {spec_id!r} "
                        f"(class {name} in {origin})"
                    )
                self._agents[spec_id] = instance
                self._logger.info(
                    "agent.registered",
                    spec_id=spec_id,
                    class_name=name,
                    underlying_model=instance.spec.underlying_model,
                )
        self._discovered = True
        if not self._agents:
            raise AgentRegistryError(
                f"no BaseAgent subclasses found in {self._folder}"
            )

    def _agent_modules(self) -> list[tuple[str, str]]:
        """(module_path, human_origin) pairs to import, sorted for
        deterministic registration order."""
        out: list[tuple[str, str]] = []
        for entry in sorted(self._folder.iterdir()):
            if entry.name.startswith("_"):
                continue
            if entry.is_dir():
                if not (entry / "__init__.py").exists():
                    continue  # not a package (e.g. __pycache__, stray dir)
                if not (entry / "agent.py").exists():
                    raise AgentRegistryError(
                        f"agent package {entry.name!r} has no agent.py — "
                        "every agent folder must expose its BaseAgent "
                        "subclass in agent.py"
                    )
                out.append(
                    (
                        f"{self._package}.{entry.name}.agent",
                        f"{entry.name}/agent.py",
                    )
                )
            elif entry.suffix == ".py":
                out.append((f"{self._package}.{entry.stem}", entry.name))
        return out

    async def validate_against(self, litellm: LiteLLMClient) -> None:
        """Cross-check each agent's spec against LiteLLM's model catalog.

        Two checks per agent:
          1. `underlying_model` is configured in LiteLLM.
          2. Every declared modality is supported by that model.

        Raises AgentRegistryError if any check fails. Called once during
        the FastAPI lifespan after `discover()` runs."""
        if not self._discovered:
            raise AgentRegistryError("validate_against() called before discover()")
        models = await litellm.list_models(force_refresh=True)
        errors: list[str] = []
        for spec_id, agent in self._agents.items():
            spec = agent.spec
            model = models.get(spec.underlying_model)
            if model is None:
                errors.append(
                    f"agent {spec_id!r}: underlying_model "
                    f"{spec.underlying_model!r} is not configured in LiteLLM"
                )
                continue
            supported = model.supported_modalities
            unsupported = [m for m in spec.accepts_modalities if m not in supported]
            if unsupported:
                errors.append(
                    f"agent {spec_id!r}: declares modalities "
                    f"{unsupported} that {spec.underlying_model!r} does "
                    f"not support (supported: {sorted(supported)})"
                )
        if errors:
            raise AgentRegistryError(
                "agent registry validation failed:\n  - "
                + "\n  - ".join(errors)
            )
        self._logger.info(
            "agent_registry.validated", count=len(self._agents)
        )

    def get(self, spec_id: str) -> BaseAgent | None:
        return self._agents.get(spec_id)

    def default_agent(self) -> BaseAgent:
        """The agent behind `default_id()`. The runner (and anything
        else that executes a thread without an explicit `agent_id`)
        goes through here — callers never touch the react_agent
        runtime directly, only the agent."""
        agent = self._agents.get(self.default_id())
        if agent is None:
            raise AgentRegistryError(
                "default_agent() called before discover() registered any agents"
            )
        return agent

    def specs(self) -> list[AgentSpec]:
        """All registered specs, sorted by id for stable ordering."""
        return [a.spec for _, a in sorted(self._agents.items())]

    def default_id(self) -> str:
        """The agent picked when a thread has no `agent_id` override.

        Convention: the agent literally named `"general"` if present,
        else the lexicographically-first registered spec id. Threads
        created BEFORE the registry rolled out will resolve to this.
        Once the platform grows beyond a single default we can make
        this configurable per-account.
        """
        if "general" in self._agents:
            return "general"
        return sorted(self._agents.keys())[0]
