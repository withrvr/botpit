FROM oven/bun:1 AS frontend-build

WORKDIR /app

COPY package.json bun.lock /app/
COPY frontend/package.json /app/frontend/

WORKDIR /app/frontend

RUN bun install

COPY ./frontend /app/frontend

ARG VITE_API_URL=

RUN bun run build


FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy

WORKDIR /app/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# Install dependencies
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-workspace --package botpit-backend

COPY ./backend/scripts /app/backend/scripts

COPY ./backend/pyproject.toml ./backend/alembic.ini /app/backend/

COPY ./backend/app /app/backend/app

COPY --from=frontend-build /app/backend/app/frontend /app/backend/app/frontend

# Sync the project
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --package botpit-backend

WORKDIR /app/backend/

# Run as a non-root user in the final image. Everything above (uv sync,
# COPY) happens as root, which is normal for the build; the app itself
# never needs to write anywhere in the image at runtime.
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home app \
    && chown -R app:app /app
USER app

# WEB_CONCURRENCY controls the worker count (default: 4, unchanged from
# before) -- overridable per deploy target instead of baked in, since the
# "right" number depends on the host's actual CPU count, which isn't known
# at build time. The default lives in the shell expansion, not a persistent
# ENV: an image-wide ENV would leak into compose.override.yml's `fastapi dev`
# too (dev's own command takes no --workers at all). `exec` replaces the
# shell so `fastapi run` receives SIGTERM directly for a clean graceful
# shutdown (see docs/development/ci-cd-and-docker.md).
CMD ["sh", "-c", "exec fastapi run --workers ${WEB_CONCURRENCY:-4}"]
