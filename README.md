# gbsynth — synthetic GrowthBook demo data

Generates realistic synthetic feature-flag/experiment/product-analytics data for four
verticals, loads it into a warehouse (Postgres or ClickHouse), and provisions a complete
GrowthBook demo workspace on top — so GrowthBook's own stats engine computes believable
experiment results for sales/marketing demos. See `PLAN.md` for the full design.

Everything here runs locally via docker-compose. Hosting on Fly.io is a later, separable
concern (the provisioner already targets a configurable host).

## Quick start

```bash
docker compose up -d                 # growthbook + mongo + postgres + clickhouse
cp .env.example .env                 # set JWT_SECRET, ENCRYPTION_KEY (openssl rand -hex 32)
uv sync

uv run gbsynth generate saas         # build + verify story outcomes via gbstats (no DB)
uv run gbsynth load saas             # + load into Postgres   (--warehouse clickhouse)
uv run gbsynth provision saas        # + build the live GrowthBook project (--warehouse clickhouse)
uv run gbsynth refresh saas          # advance data + results to today (rolling freshness)
uv run gbsynth verify saas           # pre-demo health check of the live workspace
uv run gbsynth snapshot golden       # dump a golden Mongo state
uv run gbsynth reset golden          # revert the org after a demo
uv run gbsynth cleanup saas          # tear down one vertical to rebuild it
```

Verticals: `saas`, `ecom`, `b2b`, `fintech`, `ai` (each `config/verticals/<name>.yaml`).

## Documentation

- `docs/demo-runbook.md` — sales/marketing demo guide (what to show, per vertical; daily ops)
- `docs/adding-a-vertical.md` — extend with a new vertical + full spec-field reference
- `CLAUDE.md` — dev conventions, commands, architecture map, and gotchas
- `docs/phase-2.md`, `phase0/README.md` — provisioner and validation-spike deep-dives
- `PLAN.md` — the full design; `gbsynth <cmd> --help` — per-command flags

## How it works

- **Engine** (`src/gbsynth/core/`) — vertical-agnostic. Each vertical's funnel reduces to a
  binary conversion + a value, so adding a vertical is a YAML spec, not code. Variation is
  assigned by a stable hash (no SRM); a per-user latent propensity drives behaviour (the
  CUPED hook); conversions land within the 72h window.
- **Stories** (`core/stories.py`) — a binary-search solver finds the lift that lands a
  target chance-to-win at the real sample size, then verifies the outcome against
  **gbstats**, the exact engine GrowthBook runs. Wins/losses are asserted decisively; flat
  experiments are reported (their chance-to-win is legitimately noisy).
- **Loaders** (`src/gbsynth/load/`) — idempotent Postgres (COPY, delete-by-partition) and
  ClickHouse (clickhouse-driver, MergeTree).
- **Provisioner** (`src/gbsynth/provision/`) — seeds the data source directly in Mongo
  (credentials encrypted to match GrowthBook), then creates the project, metrics, and
  experiments (backdated phases, results on stopped stories) via REST, and verifies each
  live snapshot reproduces the gbstats prediction. One warehouse database per vertical, so
  all four coexist in one org.

## Status

| Phase | Status |
|---|---|
| 0 — validation spike | done (`phase0/`) |
| 1 — engine + SaaS, gbstats-verified | done |
| 2 — provisioner (data source bootstrap, metrics, experiments, **flags**) | done (local) |
| 3 — five verticals (incl. **AI**) + ClickHouse + multi-vertical provisioning (either warehouse) | done |
| 4 — `verify`, `refresh`, `snapshot`/`reset` | done (cron is Fly-side) |
| 5 — `cleanup`, demo runbook | done (retention pruning + SDK connections pending) |

**Remaining (Fly-independent, minor):** retention pruning of old warehouse partitions;
SDK connections; true story rotation in `refresh`. **Blocked on Fly creds:** the Fly
deployment + the GitHub Actions nightly cron (reset → refresh → verify → snapshot).

## Tooling

uv (project/deps/run), ruff (lint+format), ty (types), pytest. Python 3.11 (gbstats caps
numpy<2/pandas<2). `uv run pytest` · `uv run ruff check src tests` · `uv run ty check src`.
