from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the projects service.

    All fields are sourced from environment variables (and optionally a
    `.env` file via `env_file`).  Pydantic-settings raises a clear
    `ValidationError` at startup if a required field is missing, so
    misconfigurations surface immediately rather than at request time.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ------------------------------------------------------------------
    # Required
    # ------------------------------------------------------------------

    # SQLAlchemy async DSN, e.g.:
    #   postgresql+asyncpg://user:pass@postgres:5432/projects
    database_url: str

    # Fernet symmetric encryption key for GitHub tokens.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Must be a URL-safe base64-encoded 32-byte value (44 characters).
    encryption_key: str

    # ------------------------------------------------------------------
    # Optional with sensible defaults
    # ------------------------------------------------------------------

    # CORS allowed origins.  Use ["*"] for dev; lock down in production.
    cors_origins: list[str] = ["*"]

    service_host: str = "0.0.0.0"
    service_port: int = 8003

    service_name: str = "projects-service"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
