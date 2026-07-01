from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "sandbox-service"
    service_host: str = "0.0.0.0"
    service_port: int = 8004

    # E2B credentials — required; fail loudly at boot if absent.
    e2b_api_key: str

    # CORS — allow all by default (gateway enforces auth upstream).
    cors_origins: list[str] = ["*"]

    # Default sandbox lifetime in seconds. E2B charges per second, so
    # keep this conservative; callers can override per-request.
    default_sandbox_timeout: int = 3600

    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        """Allow CORS_ORIGINS to be a comma-separated string in .env."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v


def load_env() -> Env:
    return Env()  # type: ignore[call-arg]
