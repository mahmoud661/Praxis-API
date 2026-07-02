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

# Gate: tests fail → build fails → no image → container can't start with broken code.
RUN pytest

RUN useradd -m -u 1000 app
USER app

EXPOSE 8004
# `opentelemetry-instrument` wraps the process: it discovers installed
# OpenTelemetry instrumentation packages and patches them before uvicorn starts.
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
