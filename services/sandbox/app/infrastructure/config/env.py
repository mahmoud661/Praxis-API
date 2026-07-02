from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "sandbox-service"
    service_host: str = "0.0.0.0"
    service_port: int = 8004

    # Sandbox backend: "local" (Docker containers on the host daemon, no
    # account needed) or "e2b" (E2B cloud desktop sandboxes). Defaults to
    # "local" so the stack runs end-to-end with zero external credentials.
    sandbox_provider: str = "local"

    # E2B credentials — only required when sandbox_provider == "e2b".
    # Optional otherwise so the service boots for local Docker sandboxes.
    e2b_api_key: str = ""

    # Local provider: the base image each sandbox container runs. The
    # default (built by the `sandbox-desktop-image` compose service) bundles
    # a minimal X stack so the Sandbox tab shows a live screenshot desktop.
    # Any image with a POSIX shell works for command/file execution; the
    # desktop stream additionally needs Xvfb + imagemagick + xdotool.
    local_sandbox_image: str = "praxis-sandbox-desktop:local"

    # Path to the Docker daemon socket, mounted into this container. The
    # local provider talks to the Engine API over it.
    docker_socket: str = "/var/run/docker.sock"

    # Container runtime for sandboxes. "" = Docker's default (runc). Set to
    # "sysbox-runc" to run each sandbox UNPRIVILEGED yet Docker-capable via
    # Sysbox — so the sandbox can run nested `docker`/`docker compose`
    # without host access. Requires the Sysbox runtime installed on the host
    # and registered in Docker (`docker info` lists sysbox-runc).
    sandbox_runtime: str = ""

    # Per-sandbox resource caps (local provider). Without these a single
    # runaway `npm install` or dev server can starve the host. Memory is in
    # megabytes; CPUs is a float (2.0 = two cores). 0 disables the cap.
    sandbox_memory_mb: int = 2048
    sandbox_cpus: float = 2.0
    # Hard cap on processes per sandbox — stops fork bombs cold.
    sandbox_pids_limit: int = 512

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
