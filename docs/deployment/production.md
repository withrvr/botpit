# Production deployment

**No production server exists yet.** Per the owner's explicit direction
(`DECISIONS.md` §1, amended 2026-09-12): no staging or production server is
bought or hosted until local development, setup, and testing are fully
complete first. This file describes the deployment plan for when that
phase (Phase 15 in `PROGRESS.md`'s phase table) actually happens — it is not
a description of anything currently deployed. Read `docs/deployment/staging.md`
first; production reuses the exact same mechanics against a second,
separate environment.

## Relationship to staging

Same Docker Compose files (`compose.yml` + `compose.deploy.yml`), same
image, different `.env` and a different host — deliberately **one VPS per
environment, not one box running two Compose projects**. The reasoning
(carried over from the pre-template hosting research that fed Phase 1's
Docker/compose shape): a staging load test (the join-queue behavior under
Phase 7's caps, or a many-seat all-AI table once Phase 16's scheduling work
lands) must not be able to starve production's LLM backend of RAM by
sharing a box with it.

## What changes from staging to production

- **Separate secrets.** A distinct `SECRET_KEY`, `POSTGRES_PASSWORD`,
  `FIRST_SUPERUSER_PASSWORD`, and `DOMAIN` — never shared with staging's.
- **`FASTAPI_ENV=production`.** This is the one place `FASTAPI_ENV`'s
  three-way split (`development` / `staging` / `production`) actually
  matters in application code today: `Settings._check_default_secret`
  refuses to start with a `"changethis"` placeholder secret in any
  environment except `development`, and `app/main.py` only initializes
  Sentry when `SENTRY_DSN` is set and `FASTAPI_ENV != "development"`.
- **Tighter rate limits.** `RATE_LIMIT_ROOM_CREATE`/`_JOIN`/`_MOVE` and
  `RATE_LIMIT_CHAT` default dev-generous; a production `.env` should set
  stricter values appropriate for real, possibly-adversarial traffic rather
  than a handful of trusted local testers.
- **A real backup plan.** Nightly `pg_dump` (or the hosting provider's
  managed snapshot feature) before any migration-carrying deploy. Nothing
  automated for this exists yet — it is explicitly future Phase 15 work, not
  a gap being papered over here.
- **Monitoring that actually pages someone.** Phase 13 (observability) adds
  Prometheus `/metrics`; production is where alerting on it (agent turn
  error rate per model, LLM backend degraded, capacity permanently pinned)
  would actually matter enough to wire up.

## Deploy mechanics

Identical to staging's steps 1–6 in `docs/deployment/staging.md`, against
the production host and production `.env`. The smoke-test checklist there
applies unchanged; for production, run it as a required gate before
routing real traffic, not a courtesy check.

## LLM serving for production

Same options as staging (`docs/local-model-setup.md` §3): a co-located
CPU `llama-server` by default, with `LLM_BASE_URL` repointable to an
on-demand GPU pod for a scheduled play session — an admin-adjustable
runtime setting, not a code change, per `DECISIONS.md` §2's
database-backed runtime settings design (`docs/rooms.md`). Production
capacity planning (how many concurrent games, how many AI seats at once)
follows directly from whichever LLM tier is actually running — see
`docs/local-model-setup.md` §3.2 for the CPU-inference throughput numbers
that drive this decision.

## Not decided yet

Genuinely undecided, not a stub — stated plainly rather than invented:

- Which specific host/provider (Hetzner was the pre-template research
  candidate; re-evaluate at Phase 15, prices and options will have moved).
- The actual domain name.
- Whether staging and production ever get separate LLM tiers (e.g.,
  staging on CPU only, production with an on-demand GPU pod) or share the
  same plan.
- CI/CD automation for the deploy itself (Phase 14's job — today's compose
  commands above are run by hand).
