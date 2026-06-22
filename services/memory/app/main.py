from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .application.memory_service import MemoryService
from .infrastructure.config.env import load_env
from .infrastructure.entity_provisioners import PROVISIONERS
from .infrastructure.logging.structlog_logger import StructlogLogger
from .infrastructure.memory.graphiti_store import GraphitiMemoryStore
from .infrastructure.messaging.kafka_event_consumer import KafkaEventConsumer
from .presentation.event_handlers.provision_handler import make_provisioner_handler
from .presentation.mcp_server import make_mcp_server
from .presentation.routes.knowledge_route import make_knowledge_router
from .presentation.routes.provision_route import make_provision_router


def create_app() -> FastAPI:
    env = load_env()
    logger = StructlogLogger(env.service_name)

    store = GraphitiMemoryStore(
        neo4j_uri=env.neo4j_uri,
        neo4j_user=env.neo4j_user,
        neo4j_password=env.neo4j_password,
        llm_api_key=env.litellm_proxy_api_key,
        llm_model=env.graphiti_llm_model,
        llm_base_url=env.litellm_proxy_api_base,
        llm_temperature=env.graphiti_llm_temperature,
        llm_small_model=env.graphiti_llm_small_model,
        embedding_model=env.embedding_model,
    )
    service = MemoryService(store=store, logger=logger)

    brokers = [b.strip() for b in env.kafka_brokers.split(",")]
    consumer = KafkaEventConsumer(
        brokers=brokers,
        group_id=env.kafka_group_id,
        logger=logger,
        max_attempts=env.kafka_max_handler_attempts,
    )
    for provisioner in PROVISIONERS:
        consumer.on(
            provisioner.event_name,
            make_provisioner_handler(service, provisioner),
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await store.init()
        await consumer.start(["auth.events.v1", "agents.events.v1"])
        logger.info("service.ready", service=env.service_name)
        try:
            yield
        finally:
            await consumer.stop()
            await store.close()

    app = FastAPI(title="memory-service", lifespan=lifespan)

    # MCP StreamableHTTP endpoint — ai-agents connects here at boot.
    mcp = make_mcp_server(service)
    app.mount("/mcp", mcp.streamable_http_app())

    # REST endpoints — gateway proxies /v1/knowledge/* here.
    app.include_router(make_knowledge_router(service))
    # Provision endpoints — called by other services (auth, ai-agents, etc.)
    # to register their domain entities in the knowledge graph.
    app.include_router(make_provision_router(service))

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
