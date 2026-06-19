from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "memory-service"
    port: int = 8001

    # Neo4j — required by GraphitiMemoryStore
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str

    # LLM proxy — Graphiti uses it for entity/relation extraction
    litellm_proxy_api_base: str
    litellm_proxy_api_key: str
    graphiti_llm_model: str = "gpt-4.1-mini"
    graphiti_llm_small_model: str = "gpt-4.1-nano"
    graphiti_llm_temperature: float = 0.0

    # Embedding model (must match vector dimension in Qdrant if used)
    embedding_model: str = "text-embedding-3-small"

    otel_exporter_otlp_endpoint: str | None = None

    # Kafka consumer — UserRegistered event from auth-service
    kafka_brokers: str = "kafka:9092"
    kafka_group_id: str = "memory-service"
    kafka_max_handler_attempts: int = 3


def load_env() -> Env:
    return Env()  # type: ignore[call-arg]
