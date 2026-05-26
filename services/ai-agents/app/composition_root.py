"""
Composition Root
================

Only file in the codebase allowed to import concrete adapter classes
alongside the port Protocols. Wires dependencies via constructor injection
and returns a `Container` of fully assembled objects.

Domain + application code stays free of asyncpg / aiokafka / structlog
imports — they only know about the Protocols in `domain/ports/`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .application.use_cases.create_agent import CreateAgentUseCase
from .application.use_cases.list_user_agents import ListUserAgentsUseCase
from .application.use_cases.provision_default_agent import ProvisionDefaultAgentUseCase
from .domain.ports.agent_repository import AgentRepository
from .domain.ports.event_consumer import EventConsumer
from .domain.ports.event_publisher import EventPublisher
from .domain.ports.logger import Logger
from .infrastructure.config.env import Env, load_env
from .infrastructure.logging.structlog_logger import StructlogLogger
from .infrastructure.messaging.kafka_event_consumer import KafkaEventConsumer
from .infrastructure.messaging.kafka_event_publisher import KafkaEventPublisher
from .infrastructure.persistence.postgres_agent_repository import (
    PostgresAgentRepository,
)
from .infrastructure.persistence.postgres_connection import PostgresConnection


@dataclass
class Container:
    env: Env
    logger: Logger
    pg: PostgresConnection
    agents: AgentRepository
    publisher: EventPublisher
    consumer: EventConsumer
    create_agent: CreateAgentUseCase
    list_user_agents: ListUserAgentsUseCase
    provision_default_agent: ProvisionDefaultAgentUseCase


def build_container() -> Container:
    env = load_env()
    logger: Logger = StructlogLogger(env.service_name)

    pg = PostgresConnection(env.database_url)
    agents: AgentRepository = PostgresAgentRepository(pg)

    publisher = KafkaEventPublisher(env.kafka_broker_list, env.service_name, logger)
    consumer = KafkaEventConsumer(
        env.kafka_broker_list,
        env.service_name,
        logger,
        max_attempts=env.kafka_max_handler_attempts,
    )

    create_agent = CreateAgentUseCase(agents, logger)
    list_user_agents = ListUserAgentsUseCase(agents)
    provision_default_agent = ProvisionDefaultAgentUseCase(agents, logger)

    return Container(
        env=env,
        logger=logger,
        pg=pg,
        agents=agents,
        publisher=publisher,
        consumer=consumer,
        create_agent=create_agent,
        list_user_agents=list_user_agents,
        provision_default_agent=provision_default_agent,
    )
