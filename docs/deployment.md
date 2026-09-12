# Deployment

Hosting research for Phase 14/15, done 2026-09-12 before Phase 1 so the
Docker/compose shape in Phase 1 targets a real box. Deployment mechanics
(workflows, secrets, smoke-test checklist) get filled in during Phase 14/15.

## 1. Requirements the host must meet

- Long-lived websocket connections (rules out serverless/edge-only hosts).
- Postgres, Redis, the app, and a CPU LLM server (2.5-5 GB model in RAM)
  co-located, so 8-16 GB RAM minimum for staging with a 4B model.
- Two environments with the same shape, on a side-project budget.
- Docker Compose deployable, because dev already is.

## 2. Candidates

Prices verified September 2026. Hetzner raised prices on 2026-06-15; the
numbers below are post-increase.

| Host | Shape | Monthly | Websockets | Notes |
|---|---|---|---|---|
| Hetzner CX33 (4 vCPU, 8 GB, 80 GB) | one VPS, compose | €8.49 | yes | 20 TB traffic included. Germany/Finland only. Enough for staging without the LLM, tight with it. |
| Hetzner CX43 (8 vCPU, 16 GB, 160 GB) | one VPS, compose | €15.99 | yes | Room for Postgres + Redis + app + one 4B llama-server. **Staging pick.** |
| Hetzner CAX31 (8 ARM vCPU, 16 GB) | one VPS, compose | €20.99 | yes | ARM; llama.cpp fine, but every image must be multi-arch. Not worth the friction. |
| Fly.io | machines + Fly Postgres + Upstash Redis | ~$17 for API+worker+PG at 100 GB egress; +GPU machines available | yes | Cheapest PaaS. GPU machines exist but are expensive per hour. No CPU box large enough for an LLM at that price. |
| Railway | metered | ~$46 for the same baseline | yes | Nice DX, worst price at 16 GB RAM ($10/GB-month = $160 for RAM alone). |
| Render | fixed instances | ~$37 baseline; egress cut to 5-25 GB with $0.15/GB overage | yes | Most expensive of the three PaaS for 2 GB workloads; 16 GB instances far beyond budget. |
| Hetzner GEX44 (RTX 4000 Ada 20 GB) | dedicated GPU server | €234 + €114 setup | yes | Only option that makes a 10-AI-seat table fast. Too expensive to leave running for a hobby. |
| RunPod / Vast.ai RTX 4090 | on-demand GPU pod, LLM only | $0.30-0.40/h, ~$10-30/mo for evening sessions | n/a (LLM only) | Complements a Hetzner app box; pointed at via `LLM_BASE_URL`. |

Sources: [Hetzner price adjustment 2026-06-15](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/),
[Hetzner plan table Sep 2026](https://costgoat.com/pricing/hetzner),
[PaaS spread vs Hetzner](https://bex.co/blog/2026/09/07/paas-pricing-spread-vs-hetzner-cx22-fleet),
[Render vs Railway vs Fly 2026](https://dev.to/pavel-hostim/render-vs-railway-vs-flyio-pricing-compared-2026-2e5p),
[Hetzner GEX44](https://gpuhosted.com/en/hetzner-gpu-review/),
[GPU marketplaces](https://northflank.com/blog/cheapest-cloud-gpu-providers).

## 3. Decision

**App tier: Hetzner Cloud, one VPS per environment, Docker Compose,
Caddy for TLS.**

- staging: CX43 (€15.99). Runs app (2 uvicorn workers), Postgres, Redis,
  Caddy, and one `llama-server` with a 4B Q4_K_M model on CPU.
- production: CX43 to start (€15.99), same compose file, different
  `.env`. Total for both environments: ~€32/month.
- Why Hetzner over the PaaS trio: 3-5x cheaper at the RAM we need,
  included traffic, and the LLM has to run on a box we control anyway,
  which means we are already doing "ops" (compose, backups, a Caddy
  config). Paying PaaS prices for the app while self-managing the LLM box
  gives the worst of both.
- Why one box per environment, not one box with two compose projects:
  staging load tests (Phase 7 queueing, Phase 16 ten-player games) should
  not be able to starve production's LLM of RAM.
- What we accept: no HA, no managed backups. Mitigation: nightly
  `pg_dump` to Hetzner Object Storage or a Backblaze B2 bucket (cron in
  the compose stack), and Hetzner's snapshot feature before each
  production deploy. Both get built in Phase 15.

**LLM tier: co-located CPU `llama-server` by default, optional on-demand
GPU pod for real play sessions.** See `docs/local-model-setup.md` §3.

## 4. Deploy mechanics (to be built in Phase 14/15)

- GitHub Actions `ci.yml`: ruff, mypy, pytest (SQLite + Postgres service
  container), frontend lint/build, on every PR.
- `deploy-staging.yml`: on push to `master`, build the app image, push to
  GHCR, SSH to the staging box, `docker compose pull && up -d`, run
  `alembic upgrade head`, hit `/health`.
- `deploy-production.yml`: `workflow_dispatch` or a `v*` tag, same steps
  against the production box, preceded by a Hetzner snapshot, followed by
  the smoke-test checklist.
- Secrets: GitHub environment secrets (`staging`, `production`) for the
  SSH key, `.env.<env>` contents, and the GHCR token. Nothing in the repo.
- Domains: `staging.<domain>` and `<domain>`, Caddy auto-TLS. The domain
  itself is an external blocker the owner provides before Phase 15.
