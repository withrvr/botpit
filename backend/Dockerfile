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

# Non-root user, created early so later steps can chown their own output as
# they create it. A blanket `chown -R /app` at the end (the first draft of
# this change) forces overlay2 to copy up every file from every earlier
# layer into a new one -- undoing a chunk of the slim-base size win. Owning
# each layer's output at creation time (COPY --chown, chown within the same
# RUN that populates .venv) avoids that.
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home app

WORKDIR /app/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# Install dependencies
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-workspace --package botpit-backend \
    && chown -R app:app /app/.venv

COPY --chown=app:app ./backend/scripts /app/backend/scripts

COPY --chown=app:app ./backend/pyproject.toml ./backend/alembic.ini /app/backend/

COPY --chown=app:app ./backend/app /app/backend/app

COPY --chown=app:app --from=frontend-build /app/backend/app/frontend /app/backend/app/frontend

# Sync the project
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --package botpit-backend \
    && chown -R app:app /app/.venv

WORKDIR /app/backend/

USER app

# WEB_CONCURRENCY controls the worker count (default: 4, unchanged from
# before) -- overridable per deploy target instead of baked in, since the
# "right" number depends on the host's actual CPU count, which isn't known
# at build time. The default lives in the shell expansion, not a persistent
# ENV: an image-wide ENV would leak into compose.override.yml's `fastapi dev`
# too (dev's own command takes no --workers at all). `exec` replaces the
# shell so `fastapi run` receives SIGTERM directly for a clean graceful
# shutdown (see docs/development/ci-cd-and-docker.md).
CMD ["sh", "-c", "exec fastapi run --workers \"${WEB_CONCURRENCY:-4}\""]
