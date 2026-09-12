# PROGRESS

> Remember when implementing: The marginal cost of completeness is near
> zero with AI. Do the whole thing. Do it right. Do it with tests. Do it
> with documentation. Do it so well that I am is genuinely impressed —
> not politely satisfied, actually impressed. Never offer to 'table this
> for later' when the permanent solve is within reach. Never leave a
> dangling thread when tying it off takes five more minutes. Never
> present a workaround when the real fix exists. The standard isn't
> 'good enough' — it's 'holy shit, that's done.' Search before building.
> Test before shipping. Ship the complete thing. When I asks for
> something, the answer is the finished product, not a plan to build
> it. Time is not an excuse. Fatigue is not an excuse. Complexity is not
> an excuse. Boil the ocean.

## How to resume

Read `DECISIONS.md`, then this file, then the `docs/*.md` for the subsystem
you are about to touch. Branch naming: phase branches are `phase-N-<slug>`,
task branches are `task/phase-N/<slug>` (git refuses `phase-N-slug/task-x`
when `phase-N-slug` exists as a branch). Task branches merge into the phase
branch; only the phase branch opens a PR into `master`.

## Status

| Phase | Name | Status |
|---|---|---|
| 0 | Research, decisions, proposal | done 2026-09-12 |
| 1 | Scaffold, event schema, environments | in progress |
| 2 | Game contract + UNO, Flip, No Mercy, + one more | next |
| 3 | Deterministic referee, mixed seats | |
| 4 | Privacy gate, prompt builder, human redaction | |
| 5 | Agent client, swappable backend | |
| 6 | Auth, accounts, roles/permissions | |
| 7 | Rooms, seats, capacity, join queue | |
| 8 | First live mixed game (terminal + API) | |
| 9 | Persistence and replay | |
| 10 | Frontend: play, spectate, admin, focus view | |
| 11 | Chat and personas | |
| 12 | Leaderboard | |
| 13 | Observability | |
| 14 | CI/CD (local dry-run; no server yet) | |
| 15 | Production deployment (deferred until local complete) | |
| 16 | Scale and polish | |

## Phase 1 — in progress

Done:
- Research: `docs/local-model-setup.md`, `docs/deployment.md`.
- `pyproject.toml`, uv, ruff, mypy strict, pytest; `src/botpit/config.py`;
  `.env.dev`, `.env.staging.example`, `.env.production.example`.
- Task worktrees dispatched: logging, events (schema/filter/bus x2), db
  (SQLAlchemy + Alembic), llm (health probe), infra (compose/Dockerfile/Caddy).

In progress:
- Task branches above; then app factory, request middleware, CI workflow.

Open decisions raised mid-build:
- GitHub branch protection on `master` is unavailable (private repo on the
  free plan). Protection is by discipline: PR-only merges, never push to
  master directly.
- Original `DECISIONS.md` missing; reconstructed one committed. Owner to
  supply the original for a merge.
