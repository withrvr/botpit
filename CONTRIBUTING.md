# Contributing to botpit

Thanks for your interest in botpit.

## Setup

Follow [`docs/development/setup.md`](docs/development/setup.md) to get a
local backend, frontend, Postgres, Redis, and Ollama running. This project
is local-development only right now — there's no staging or production
environment to target.

## Branch and PR conventions

- Each phase of work gets a phase branch off `master`: `phase-N-<slug>`
  (e.g. `phase-9-game-history`).
- Work within a phase happens on task branches off the phase branch:
  `task/phase-N/<slug>`. Task branches merge into their phase branch, not
  into `master`.
- Only the phase branch opens a pull request into `master`. Never push to
  `master` directly.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `refactor:`, etc.).

## Before opening a pull request

Run the full gate from the repo root and make sure it's clean:

```bash
uv run prek run --all-files   # ruff check, ruff format, mypy strict, ty
cd backend && uv run pytest tests
```

Add `-m integration` (with a running `redis` from `docker compose up -d redis`)
to also exercise the Redis-backed event bus, and `-m ollama` if you have a
reachable Ollama with the models listed in `docs/local-model-setup.md`
configured — neither marker runs by default.

## Pull requests

- Keep a PR focused on the phase (or task) it represents.
- Update or add tests for any behavior change.
- Reference the relevant section of `DECISIONS.md` or `PROGRESS.md` when a
  change touches a documented decision or a phase's scope.

## Questions

Open a GitHub issue or discussion on this repository.
