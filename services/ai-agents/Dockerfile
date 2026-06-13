# Python service image. Tests run during the build — failure aborts the image.

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# uv from its official image (multi-arch, single binary).
COPY --from=ghcr.io/astral-sh/uv:0.7.14 /uv /uvx /usr/local/bin/

# Install everything (incl. dev/pytest) locked to uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --extra dev

COPY app ./app
COPY tests ./tests
COPY pytest.ini ./

# Gate: tests fail → build fails → no image → container can't start with
# broken code. Runs offline (in-memory fakes only; no Kafka / Postgres).
RUN pytest

RUN useradd -m -u 1000 app

# Pre-create the file-storage dir AND chown it to `app` BEFORE the
# volume mount overlays it. Docker initialises a fresh named volume
# with the contents (and ownership) of the mount point, so this gives
# the volume `app:app` ownership on first use. Without this, the
# volume starts root-owned and uploads fail with PermissionError
# the moment the app tries to create a subdir under it.
RUN mkdir -p /var/lib/praxis/files && chown -R app:app /var/lib/praxis/files

USER app

EXPOSE 8000
# `opentelemetry-instrument` wraps the process: it discovers installed
# OpenTelemetry instrumentation packages and patches them before uvicorn starts.
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
