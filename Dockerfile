# syntax=docker/dockerfile:1
# Two stages: uv resolves and installs into /app/.venv, the runtime image only
# carries the venv and the source tree. Same image serves dev (with the compose
# override bind-mounting src), staging and production.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies first so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# migrations/ and alembic.ini are produced by the migrations task; the build
# fails until they exist, which is intended (an image without them cannot run
# `alembic upgrade head` on deploy).
COPY src ./src
COPY alembic.ini README.md ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --gid 10001 botpit && useradd --uid 10001 --gid botpit --no-create-home botpit
COPY --from=builder --chown=botpit:botpit /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
USER botpit
EXPOSE 8000
# No curl in slim; stdlib urllib is enough. urlopen raises on non-2xx anyway.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"
CMD ["uvicorn", "botpit.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
