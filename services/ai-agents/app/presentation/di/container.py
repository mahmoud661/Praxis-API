"""
Composition Root (DDD bootstrap layer)
======================================

The only place that imports concrete adapters together with their port tokens.
Auto-wires the app by globbing the `repos/` and `application/services/`
folders, dynamic-importing each module, and registering every class to a
domain-interface token by naming convention:

    repos:    AgentRepo    -> "IAgentRepo"    ("I" + name w/o "Repo" + "Repo")
    services: AgentService -> "IAgentService" ("I" + class name)

Consumers declare what they need by that token via type-annotated `__init__`
parameters. The container reads the annotations with `get_type_hints` and
resolves each by the annotation's `__name__`.

Only genuine infrastructure adapters (logger, event publisher/consumer,
the agentic store) and the entry-point classes (controllers, routes) are
registered explicitly — same pattern as the auth service.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any, Callable, get_type_hints

from redis.asyncio import Redis

from ...application.services.agentic.main_agent import MainAgent
from ...application.services.agentic.run_manager import RunManager
from ...application.services.agentic.runner import AgentRunner
from ...domain.ports.event_consumer import EventConsumer
from ...domain.ports.event_publisher import EventPublisher
from ...domain.ports.logger import Logger
from ...infrastructure.agentic.agentic_store import AgenticStore
from ...infrastructure.ai.title_generator import TitleGenerator
from ...infrastructure.cache.event_stream import EventStream
from ...infrastructure.config.env import load_env
from ...infrastructure.logging.structlog_logger import StructlogLogger
from ...infrastructure.messaging.kafka_event_consumer import KafkaEventConsumer
from ...infrastructure.messaging.kafka_event_publisher import KafkaEventPublisher
from ..controllers.agents_runs_controller import AgentsRunsController
from ..controllers.health_controller import HealthController
from ..controllers.threads_controller import ThreadsController
from ..controllers.turns_controller import TurnsController
# Routes are auto-mounted by `mount_routes()` scanning presentation/routes/.
# These imports are unused locally but they make the route classes discoverable
# even if a future packaging step tree-shakes unreferenced modules.
from ..routes.agents_runs_route import AgentsRunsRoute  # noqa: F401
from ..routes.agents_ws_route import AgentsWsRoute  # noqa: F401
from ..routes.notifications_ws_route import NotificationsWsRoute  # noqa: F401
from ..routes.threads_route import ThreadsRoute  # noqa: F401
from ..routes.turns_route import TurnsRoute  # noqa: F401

_APP_ROOT = Path(__file__).resolve().parents[2]  # .../app/


class Container:
    """Minimal DI container. Tokens are strings (class names of interfaces),
    instances are cached singletons."""

    def __init__(self) -> None:
        self._bindings: dict[str, Any] = {}

    def register(self, token: str, value: Any) -> None:
        self._bindings[token] = value

    def has(self, token: str) -> bool:
        return token in self._bindings

    def resolve(self, token: str) -> Any:
        if token not in self._bindings:
            raise KeyError(f"No binding registered for {token!r}")
        return self._bindings[token]

    def auto_register(
        self,
        *,
        package: str,
        folder: Path,
        token_fn: Callable[[type], str],
    ) -> None:
        """Glob every `.py` file in `folder`, dynamic-import via `package`,
        and register each *locally-defined* class under `token_fn(class)`."""
        for py_file in sorted(folder.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            mod_name = f"{package}.{py_file.stem}"
            module = importlib.import_module(mod_name)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Only register classes *defined* in this module — skip
                # things merely imported (e.g. Logger, IAgentRepo).
                if obj.__module__ != module.__name__:
                    continue
                # Skip private helpers (leading underscore convention).
                if name.startswith("_"):
                    continue
                token = token_fn(obj)
                self.register(token, self.construct(obj))

    def construct(self, cls: type) -> Any:
        """Instantiate `cls` by reading its `__init__` annotations and
        resolving each from this container by the annotation's `__name__`."""
        try:
            hints = get_type_hints(cls.__init__)
        except Exception:  # noqa: BLE001
            hints = {}
        sig = inspect.signature(cls.__init__)
        kwargs: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            # Classes without an explicit __init__ inherit `object.__init__`,
            # whose signature includes `*args, **kwargs` — skip those, they
            # aren't real dependencies.
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            anno = hints.get(name)
            if anno is None:
                raise RuntimeError(
                    f"{cls.__name__}.__init__ parameter {name!r} has no "
                    f"resolvable type annotation; the DI container needs one "
                    f"to look up a binding."
                )
            token = getattr(anno, "__name__", str(anno))
            kwargs[name] = self.resolve(token)
        return cls(**kwargs)


def register_dependencies() -> Container:
    """Wire the whole graph and hand the container back to `main.py`.
    AgenticStore is registered but NOT yet connected — the FastAPI lifespan
    calls `await agentic_store.init()` to open the pool and run `.setup()`."""
    container = Container()

    env = load_env()
    container.register("Env", env)

    logger: Logger = StructlogLogger(env.service_name)
    container.register("Logger", logger)

    # The agentic persistence stack (LangGraph Postgres Store + Checkpointer).
    # Replaces SQLAlchemy + create_all + Alembic in one shot.
    container.register("AgenticStore", AgenticStore(env))

    # Redis client (lazy connect on first use). Powers both the per-run
    # EventStream and the per-user notifications pub/sub.
    redis = Redis.from_url(env.redis_url, decode_responses=True)
    container.register("Redis", redis)

    # EventStream — Redis Streams-backed per-run event bus. Replaces the old
    # list+pubsub RedisCache: same key serves "replay from offset 0" AND
    # "block waiting for new entries", so reconnecting clients see no
    # duplicates and no misses.
    container.register("EventStream", EventStream(redis))

    # Repos auto-register here so the manual constructions below (RunManager
    # needs IThreadRepo) can resolve them. The repos themselves only depend
    # on AgenticStore + Logger, both registered above.
    container.auto_register(
        package="app.infrastructure.database.repos",
        folder=_APP_ROOT / "infrastructure" / "database" / "repos",
        token_fn=lambda cls: "I" + cls.__name__.replace("Repo", "") + "Repo",
    )

    # MainAgent compiles its graph lazily on first use, so it can be built
    # here before AgenticStore.init() has connected — get() defers _build()
    # until after the lifespan has wired the checkpointer.
    container.register("MainAgent", MainAgent(container.resolve("AgenticStore"), env))

    # AgentRunner: streams events from the LangGraph graph through an opaque
    # `on_event` callback. The RunManager wires that callback to the
    # EventStream so a WebSocket disconnect doesn't kill the run.
    container.register(
        "AgentRunner",
        AgentRunner(container.resolve("MainAgent"), logger),
    )

    # RunManager: owns the background asyncio.Task per thread, the running
    # set in Redis, and the notifications pub/sub. The Task survives WS
    # disconnects; clients reconnect and replay the EventStream.
    container.register(
        "RunManager",
        RunManager(
            container.resolve("AgentRunner"),
            container.resolve("EventStream"),
            redis,
            logger,
            container.resolve("IThreadRepo"),
        ),
    )

    publisher: EventPublisher = KafkaEventPublisher(
        env.kafka_broker_list, env.service_name, logger
    )
    container.register("EventPublisher", publisher)

    consumer: EventConsumer = KafkaEventConsumer(
        env.kafka_broker_list,
        env.service_name,
        logger,
        max_attempts=env.kafka_max_handler_attempts,
    )
    container.register("EventConsumer", consumer)

    # TitleGenerator — small LLM wrapper for auto-naming brand-new
    # threads. Registered BEFORE services auto-register because
    # ThreadsService depends on it (via its `title_generator: TitleGenerator`
    # constructor param resolved by class-name lookup).
    container.register("TitleGenerator", TitleGenerator(env, logger))

    # Auto-discover services: AuthService -> "IAuthService". Threads service
    # depends on IThreadRepo (already registered above) + AgenticStore +
    # Logger + TitleGenerator + Redis, all resolvable here.
    container.auto_register(
        package="app.application.services",
        folder=_APP_ROOT / "application" / "services",
        token_fn=lambda cls: "I" + cls.__name__,
    )

    # Wire post-turn hooks now that BOTH RunManager and ThreadsService
    # have been constructed. ThreadsService.maybe_generate_title runs
    # after every run.end — it short-circuits if the thread already has
    # a non-default title, so subsequent turns pay nothing.
    threads_service = container.resolve("IThreadsService")
    run_manager: RunManager = container.resolve("RunManager")
    run_manager.register_post_turn_hook(
        lambda thread_id, owner_id: threads_service.maybe_generate_title(
            thread_id=thread_id, owner_id=owner_id
        )
    )

    # Entry-point classes — registered by their own class name. The auto-
    # mounted routes (AgentsWsRoute, NotificationsWsRoute, AgentsRunsRoute,
    # ThreadsRoute) resolve their dependencies from these registrations
    # during boot.
    container.register(
        "HealthController", HealthController(container.resolve("AgenticStore"))
    )
    container.register(
        "AgentsRunsController",
        AgentsRunsController(container.resolve("RunManager")),
    )
    container.register(
        "ThreadsController",
        ThreadsController(container.resolve("IThreadsService")),
    )
    container.register(
        "TurnsController",
        TurnsController(container.resolve("ITurnsService")),
    )

    return container


def mount_routes(app, container: Container) -> None:
    """Glob `presentation/routes/*.py`, instantiate each `BaseRoute` subclass
    via the container (so its controller dependency is injected), and mount
    its router at the route's `path`."""
    from ..routes.base_route import BaseRoute

    routes_folder = _APP_ROOT / "presentation" / "routes"
    for py_file in sorted(routes_folder.glob("*.py")):
        if py_file.name.startswith("_") or py_file.stem == "base_route":
            continue
        module = importlib.import_module(f"app.presentation.routes.{py_file.stem}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue
            if not issubclass(obj, BaseRoute) or obj is BaseRoute:
                continue
            route: BaseRoute = container.construct(obj)
            app.include_router(route.router, prefix=route.path)
            container.resolve("Logger").info("route.mounted", path=route.path or "/")


__all__ = ["Container", "register_dependencies", "mount_routes"]
