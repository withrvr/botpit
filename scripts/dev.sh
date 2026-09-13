#!/usr/bin/env bash
#
# dev.sh — one-command local dev environment for botpit, in a tmux session.
#
# Windows created (session name: botpit-dev):
#   backend   FastAPI with reload: `uv run bash scripts/prestart.sh && uv run
#             fastapi dev`, run from backend/ (matches README's quick start).
#   frontend  Vite dev server: `bun install && bun run dev`, run from
#             frontend/. Skipped with a one-line message if frontend/ has no
#             package.json or no "dev" script (pre-Phase-10 state).
#   compose   Ensures Postgres + Redis (compose services `db`, `redis`) are
#             up via `docker compose up -d --wait db redis` — only if they
#             aren't already running, so re-running this script doesn't spam
#             `up` — then shows `docker compose ps` and drops to a shell.
#   logs      Follows `docker compose logs -f db redis`. The backend has no
#             LOG_FILE configured by default (see backend/app/core/config.py,
#             docs/admin-and-observability.md) — it logs to stdout only,
#             already visible live in the backend window — so this window
#             tails the compose-managed services instead. If LOG_FILE is set
#             in the environment and exists, it tails that file instead.
#
# Idempotent: if the tmux session already exists, attaches to it rather than
# creating a duplicate. Always ends by attaching, so `./scripts/dev.sh` lands
# you directly in the multi-window view. Safe to run from any directory.
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SESSION="botpit-dev"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already running — attaching."
  exec tmux attach-session -t "$SESSION"
fi

BACKEND_CMD='uv run bash scripts/prestart.sh && uv run fastapi dev'

COMPOSE_CMD='
cd '"$REPO_ROOT"'
running=$(docker compose ps --status running --services db redis 2>/dev/null || true)
missing=""
for s in db redis; do
  echo "$running" | grep -qx "$s" || missing="$missing $s"
done
if [ -n "$missing" ]; then
  echo "Starting:$missing"
  docker compose up -d --wait db redis
else
  echo "db and redis already running."
fi
docker compose ps
exec "${SHELL:-bash}"
'

LOGS_CMD='
if [ -n "${LOG_FILE:-}" ] && [ -f "${LOG_FILE:-}" ]; then
  echo "Tailing LOG_FILE=$LOG_FILE"
  exec tail -f "$LOG_FILE"
fi
echo "No LOG_FILE configured (backend logs to stdout only — see the backend window)."
echo "Following Postgres/Redis container logs instead:"
cd '"$REPO_ROOT"'
exec docker compose logs -f db redis
'

tmux new-session -d -s "$SESSION" -n backend -c "$REPO_ROOT/backend" "$BACKEND_CMD"

if [ -f "$REPO_ROOT/frontend/package.json" ] && grep -q '"dev"[[:space:]]*:' "$REPO_ROOT/frontend/package.json"; then
  tmux new-window -t "$SESSION" -n frontend -c "$REPO_ROOT/frontend" 'bun install && bun run dev'
else
  echo "frontend not yet scaffolded (Phase 10) — skipping frontend window"
fi

tmux new-window -t "$SESSION" -n compose -c "$REPO_ROOT" "$COMPOSE_CMD"
tmux new-window -t "$SESSION" -n logs -c "$REPO_ROOT" "$LOGS_CMD"

tmux select-window -t "$SESSION:backend"

exec tmux attach-session -t "$SESSION"
