from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "ai-agents-service"
    port: int = 8000

    database_url: str
    kafka_brokers: str  # comma-separated
    # Redis holds the per-run event buffer the AgentRunner streams into.
    # Cleared when the run ends (success or error).
    redis_url: str

    # LiteLLM proxy is the model provider — exposes an OpenAI-compatible
    # API in front of whichever upstream model is configured on the proxy.
    # The agent's `ChatOpenAI` client points at `litellm_proxy_api_base` with
    # `litellm_proxy_api_key`, and asks for `litellm_model` by name.
    litellm_proxy_api_base: str
    litellm_proxy_api_key: str
    litellm_model: str
    # Master key — used by admin tooling against the proxy, not by the
    # agent's chat client. Loaded so it's available if/when we need it.
    litellm_master_key: str | None = None

    # Legacy direct-provider keys — kept optional in case a future code path
    # needs them, but the active agent goes through the LiteLLM proxy above.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    otel_exporter_otlp_endpoint: str | None = None

    # How many times the Kafka consumer retries a handler before DLQ.
    kafka_max_handler_attempts: int = 3

    # File storage backend. `local` (default) writes to `files_local_dir`;
    # `memory` is dict-backed (tests only); `s3` is interface-only today
    # (see `infrastructure/files/file_storage.py` — raises at construction
    # so a misconfig fails loud at boot).
    files_storage_backend: str = "local"
    # Where LocalFileStorage writes. Volume-mounted in production so
    # files survive pod restarts; defaults to a path inside the
    # container that compose can mount.
    files_local_dir: str = "/var/lib/praxis/files"

    @property
    def kafka_broker_list(self) -> list[str]:
        return [b.strip() for b in self.kafka_brokers.split(",") if b.strip()]


def load_env() -> Env:
    return Env()  # type: ignore[call-arg]
