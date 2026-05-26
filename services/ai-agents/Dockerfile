# Python service image. Tests run during the build — failure aborts the image.

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# uv from its official image (multi-arch, single binary).
COPY --from=ghcr.io/astral-sh/uv:0.7.14 /uv /uvx /usr/local/bin/

# Install everything (incl. pytest) into the system Python.
COPY requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

COPY app ./app
COPY tests ./tests
COPY pytest.ini ./
COPY alembic.ini ./
COPY migrations ./migrations

# Gate: tests fail → build fails → no image → container can't start with
# broken code. Runs offline (in-memory fakes only; no Kafka / Postgres).
RUN pytest

RUN useradd -m -u 1000 app
USER app

EXPOSE 8000
# `opentelemetry-instrument` wraps the process: it discovers installed
# OpenTelemetry instrumentation packages and patches them before uvicorn starts.
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
