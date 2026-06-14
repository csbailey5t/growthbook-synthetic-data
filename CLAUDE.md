# CLAUDE.md — gbsynth project conventions

Synthetic GrowthBook demo-data generator. Generates verified experiment data for several
verticals, loads it into Postgres or ClickHouse, and provisions a live GrowthBook workspace
on top. Overview: `README.md`. Design: `PLAN.md`. Demo guide: `docs/demo-runbook.md`.
Add a vertical: `docs/adding-a-vertical.md`.

## Stack & commands

Astral toolchain. **Python is pinned to 3.11** (see gotcha below).

```bash
uv sync                              # install deps
uv run pytest -q                     # tests (DB-free; ~4s)
uv run ruff check src tests          # lint
uv run ruff format src tests         # format
uv run ty check src tests            # type check
uv run gbsynth <cmd> --help          # per-command flags
```

Run the quality gate (ruff + ruff format + ty + pytest) before committing — it's been clean
on every commit.

## Local stack

`docker compose up -d` brings up growthbook (4.4.0, pinned), mongo, postgres, clickhouse.
Secrets live in `.env` (gitignored; copy `.env.example`). After first-run GrowthBook setup
in the UI, create an admin secret key → `.env` as `GB_API_KEY`. `ENCRYPTION_KEY` must match
the running app (it decrypts seeded data-source credentials).

## Architecture map

- `src/gbsynth/spec.py` — pydantic vertical-spec models (the YAML contract).
- `src/gbsynth/core/` — vertical-agnostic engine: `population` (personas + propensity +
  signup curve), `experiments` (hash assignment + disjoint windows + effect),
  `sessions` (events), `stories` (lift solver + gbstats verification).
- `src/gbsynth/schemas/` — output tables (`segment`: experiment_viewed/identifies incl.
  `context_*`; `common`: users/tracks).
- `src/gbsynth/load/` — `postgres` (COPY, delete-by-partition) and `clickhouse` (native
  clickhouse-driver, MergeTree).
- `src/gbsynth/provision/` — `crypto` (crypto-js-compatible AES), `bootstrap` (Mongo
  data-source seeding), `client` (REST), `metrics`, `experiments`, `features`, `workspace`,
  `provisioner` (orchestrator).
- `src/gbsynth/{build,refresh,reset,cleanup}.py` — pipeline + ops.
- `src/gbsynth/cli.py` — `generate / load / provision / refresh / verify / snapshot / reset / cleanup`.
- `phase0/` — the disposable validation spike (kept for reference).

## Conventions & gotchas (hard-won — read before changing these)

- **Python 3.11, not 3.12.** `gbstats` caps `numpy<2` / `pandas<2`, which have no cp312
  wheels. Don't bump Python or unpin numpy/pandas without re-checking gbstats.
- **Verify outcomes against the real `gbstats`**, never a reimplementation — that's what
  guarantees the offline prediction matches the live GrowthBook snapshot.
- **Decisive-only assertion.** A true-zero-effect experiment's chance-to-win is uniformly
  distributed regardless of N, so only win/loss (CTW near the rails) are asserted; flat
  stories are reported. Same logic applies to live-vs-predicted checks.
- **NUMERIC → double precision.** The Postgres driver returns NUMERIC as strings, which
  GrowthBook types as `string` and refuses to `sum`. Value columns are `double precision`
  (warehouse) / cast at fact-table SQL.
- **ClickHouse:** no `public` schema (Segment SQL uses bare table names for CH); load via
  `clickhouse-driver` (native :9000), not `clickhouse-connect` (needs pandas≥2).
- **One warehouse database per vertical**, identically-named tables inside, so all verticals
  coexist in one GrowthBook org. ids are namespaced (`fact__<vertical>_*`, `ft_<vertical>_tracks`).
- **Experiments have no REST `DELETE`.** `cleanup` removes them via Mongo; provisioning
  reuses-by-tracking-key (and `_find` paginates — don't revert that).
- **Determinism:** everything keys off `seed` + `now`. Don't introduce wall-clock randomness
  in generation.

## Status

Phases 0–5 implemented and validated locally (five verticals: saas/ecom/b2b/fintech/ai,
Postgres or ClickHouse). **Blocked on Fly.io credentials only:** the Fly deployment + the
GitHub Actions nightly cron. The provisioner already targets a configurable host. Minor
Fly-independent TODO: retention pruning, SDK connections, true story rotation in `refresh`.
