from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "ai-agents-service"
    port: int = 8000

    database_url: str
    kafka_brokers: str  # comma-separated

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    otel_exporter_otlp_endpoint: str | None = None

    # How many times the Kafka consumer retries a handler before DLQ.
    kafka_max_handler_attempts: int = 3

    @property
    def kafka_broker_list(self) -> list[str]:
        return [b.strip() for b in self.kafka_brokers.split(",") if b.strip()]


def load_env() -> Env:
    return Env()  # type: ignore[call-arg]
