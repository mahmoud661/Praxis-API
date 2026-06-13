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

from ...application.services.agentic.agent_registry import AgentRegistry
from ...application.services.agentic.run_manager import RunManager
from ...application.services.agentic.runner import AgentRunner
from ...domain.ports.event_consumer import EventConsumer
from ...domain.ports.event_publisher import EventPublisher
from ...domain.ports.logger import Logger
from ...infrastructure.agentic.agentic_store import AgenticStore
from ...infrastructure.ai.title_generator import TitleGenerator
from ...infrastructure.cache.event_stream import EventStream
from ...infrastructure.config.env import load_env
from ...infrastructure.documents.document_extractor import DocumentExtractor
from ...infrastructure.files.file_storage import (
    IFileStorage,
    InMemoryFileStorage,
    LocalFileStorage,
    S3FileStorage,
)
from ...infrastructure.llm.embedding_client import EmbeddingClient
from ...infrastructure.llm.litellm_client import LiteLLMClient
from ...infrastructure.vector.in_memory_vector_store import InMemoryVectorStore
from ...infrastructure.vector.qdrant_vector_store import QdrantVectorStore
from ...infrastructure.logging.structlog_logger import StructlogLogger
from ...infrastructure.messaging.kafka_event_consumer import KafkaEventConsumer
from ...infrastructure.messaging.kafka_event_publisher import KafkaEventPublisher
from ..controllers.agents_runs_controller import AgentsRunsController
from ..controllers.capabilities_controller import CapabilitiesController
from ..controllers.files_controller import FilesController
from ..controllers.health_controller import HealthController
from ..controllers.threads_controller import ThreadsController
from ..controllers.turns_controller import TurnsController
from ..http.ws_connection_registry import WsConnectionRegistry
# Routes are auto-mounted by `mount_routes()` scanning presentation/routes/.
# Importing each module triggers its `BaseRoute` subclass registration.
# The `as X` re-export form signals to linters and static analysers that
# these imports are intentional, preventing false unused-import alerts.
from ..routes.agents_runs_route import AgentsRunsRoute as AgentsRunsRoute
from ..routes.agents_ws_route import AgentsWsRoute as AgentsWsRoute
from ..routes.capabilities_route import CapabilitiesRoute as CapabilitiesRoute
from ..routes.files_route import FilesRoute as FilesRoute
from ..routes.notifications_ws_route import NotificationsWsRoute as NotificationsWsRoute
from ..routes.threads_route import ThreadsRoute as ThreadsRoute
from ..routes.turns_route import TurnsRoute as TurnsRoute

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
                # Skip if a binding for this token is already in place —
                # lets the composition root pre-register a service
                # manually (in dep-order) to break cycles the
                # alphabetical glob can't handle on its own.
                if self.has(token):
                    continue
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

    # LiteLLM admin client — used by the agent registry's boot-time
    # validation AND the capabilities service for per-agent
    # `underlying` metadata. Single instance, cached internally.
    container.register(
        "LiteLLMClient",
        LiteLLMClient(
            base_url=env.litellm_proxy_api_base,
            master_key=env.litellm_master_key,
            logger=logger,
        ),
    )

    # Agent registry built here BUT discovery deferred to after the
    # services auto-register below — agents depend on IFilesService /
    # IKnowledgeService which don't exist until then. The registry
    # itself goes into the container right away because downstream
    # services (CapabilitiesService) take it as an __init__ dep.
    registry = AgentRegistry(
        agents_folder=(
            _APP_ROOT / "application" / "services" / "agentic" / "agents"
        ),
        logger=logger,
        constructor=container.construct,
    )
    container.register("AgentRegistry", registry)

    # Attachment captioner — the app's implementation of the react_agent
    # library's CaptionModel port (LiteLLM-backed). Agents resolve it by
    # class-name annotation, so it must exist before registry.discover().
    from ...infrastructure.llm.attachment_captioner import AttachmentCaptioner

    container.register("AttachmentCaptioner", AttachmentCaptioner(env, logger))

    # File storage backend — picked by env var. Local is the default
    # and matches our single-pod compose layout; S3 is interface-only
    # today (its constructor raises with instructions). InMemory exists
    # for tests / dev smoke runs.
    storage: IFileStorage
    backend = env.files_storage_backend.lower()
    if backend == "memory":
        storage = InMemoryFileStorage()
    elif backend == "s3":
        # Constructor raises NotImplementedError with a clear message.
        # We instantiate at boot so a misconfig fails loudly here,
        # not on the first upload.
        storage = S3FileStorage(bucket="praxis-files")
    else:
        storage = LocalFileStorage(env.files_local_dir, logger)
    container.register("IFileStorage", storage)

    # Document extractor — stateless, no DI deps. One instance shared
    # across all callers (the read_attachment tool, KnowledgeService).
    container.register("IDocumentExtractor", DocumentExtractor())

    # Embedding client — talks to the LiteLLM proxy's /embeddings
    # endpoint. Same auth/base-url as the chat client. Holds an
    # httpx.AsyncClient with keep-alive; closed via lifespan shutdown.
    container.register("IEmbeddingClient", EmbeddingClient(env, logger))

    # Vector store — Qdrant by default; InMemory for dev/test setups
    # without Qdrant running. The collection is created on first
    # `ensure_ready()` call from the lifespan startup hook.
    if env.vector_store_backend.lower() == "memory":
        container.register("IVectorStore", InMemoryVectorStore())
    else:
        container.register("IVectorStore", QdrantVectorStore(env, logger))

    # Pre-register KnowledgeService + FilesService manually so the
    # AgentRunner construction below can resolve IFilesService. The
    # alphabetical auto_register globber would build FilesService BEFORE
    # KnowledgeService (FilesService depends on IKnowledgeService) and
    # fail. Pre-registering both here in dep-order breaks the cycle;
    # the later auto_register skips them via the has-token short-circuit.
    from ...application.services.content_reference_lookup_service import (
        ContentReferenceLookupService,
    )
    from ...application.services.files_service import FilesService
    from ...application.services.knowledge_service import KnowledgeService

    container.register("IKnowledgeService", container.construct(KnowledgeService))
    container.register("IFilesService", container.construct(FilesService))
    # Pre-register under the port's name `IContentReferenceLookup` so
    # `ContentReferenceMiddleware` resolves it. Class name doesn't
    # match the `I + cls.__name__` convention (port is shorter than
    # impl class name), so we do this by hand.
    container.register(
        "IContentReferenceLookup",
        container.construct(ContentReferenceLookupService),
    )

    # AgentRunner: streams events from the LangGraph graph through an opaque
    # `on_event` callback. Resolves the agent to execute via the registry
    # (default agent today; per-thread agent_id later) — it never touches
    # the react_agent runtime directly. The RunManager wires `on_event` to
    # the EventStream so a WebSocket disconnect doesn't kill the run.
    container.register(
        "AgentRunner",
        AgentRunner(
            registry,
            container.resolve("IFilesService"),
            logger,
        ),
    )

    # RunManager: owns the background asyncio.Task per thread, the running
    # set in Redis, and the notifications pub/sub. The Task survives WS
    # disconnects; clients reconnect and replay the EventStream. The cap
    # bounds active+queued turns per user across all their threads.
    container.register(
        "RunManager",
        RunManager(
            container.resolve("AgentRunner"),
            container.resolve("EventStream"),
            redis,
            logger,
            container.resolve("IThreadRepo"),
            max_concurrent_runs_per_user=env.max_concurrent_runs_per_user,
        ),
    )

    # WS connection registry — per-user cap on open agents-WS sockets.
    # In-memory is authoritative because the service is single-process;
    # AgentsWsRoute resolves it by annotation class name.
    container.register(
        "WsConnectionRegistry",
        WsConnectionRegistry(env.max_ws_connections_per_user),
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

    # Agents discover NOW — after services auto-register so an agent's
    # __init__ can resolve IFilesService, IKnowledgeService, etc.
    # `.validate_against(litellm)` is async and stays deferred to the
    # lifespan; this call is the synchronous instantiation pass.
    registry.discover()

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
    container.register(
        "CapabilitiesController",
        CapabilitiesController(container.resolve("ICapabilitiesService")),
    )
    container.register(
        "FilesController",
        FilesController(container.resolve("IFilesService")),
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
