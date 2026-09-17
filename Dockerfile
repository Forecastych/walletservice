# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Base: the uv binary and the settings every stage shares.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS base

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /srv/app

# ---------------------------------------------------------------------------
# Builder: resolve production dependencies into a self-contained virtualenv.
# ---------------------------------------------------------------------------
FROM base AS builder

# Dependencies change far less often than source, so they get their own layer.
COPY pyproject.toml README.md ./
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

# ---------------------------------------------------------------------------
# Runtime: no build tooling, no package manager, no root.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# curl is needed by the container healthcheck and nothing else.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv/app
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health/live || exit 1

# Logging is configured by the application itself (structured JSON); uvicorn's
# own access log is disabled in favour of the request middleware's.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--no-access-log"]

# ---------------------------------------------------------------------------
# Dev: production dependencies plus the test and lint toolchain.
# Used by `docker compose --profile test` and by CI.
# ---------------------------------------------------------------------------
FROM base AS dev

# psql/pg_isready let the test entrypoint create and wait for its own database.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache -e ".[dev]"

COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests

CMD ["pytest"]
