# Phase 0 — Validation spike runbook

**Goal:** prove, end-to-end and locally, that synthetic warehouse data makes GrowthBook's
own stats engine compute a believable experiment result with **no SRM or multiple-exposure
warnings** — and that the workspace can be reset to a golden state. This de-risks every
later phase before any Fly.io / cloud investment. (See `PLAN.md` Phase 0, lines 269-277.)

Everything here runs on your machine via docker compose. No Fly.io credentials needed.

## What this spike validated (results)

Run against GrowthBook **4.4.0**, Postgres 16, Mongo 8.0:

| Check | Result |
|---|---|
| Variation split | 2,504 / 2,496 (50.08% / 49.92%) |
| **SRM** | p = 0.910 → **no mismatch** (warns only below 0.001) |
| **Multiple exposures** | none detected |
| Purchase conversion (proportion metric) | 27.2% → 31.1%, **99.8%** chance to win, +14.6% |
| Revenue per user (mean metric) | 14.98 → 18.15, **100%** chance to win, +21.1% |
| Health tab | **"No issues found. 🎉"** |
| Reset loop | junk flag created → `reset restore` → junk gone, experiment intact |

Screenshots: [`screenshots/phase0-results.png`](screenshots/phase0-results.png),
[`screenshots/phase0-health.png`](screenshots/phase0-health.png).

## Prerequisites

- Docker + Docker Compose (daemon running)
- [`uv`](https://docs.astral.sh/uv/) (Astral) — manages Python, deps, and execution

## Runbook

### 1. Configure local secrets

```bash
cp .env.example .env
# Generate the two GrowthBook secrets and paste them into .env:
openssl rand -hex 32   # -> JWT_SECRET
openssl rand -hex 32   # -> ENCRYPTION_KEY
```

`ENCRYPTION_KEY` encrypts the data-source credentials in Mongo and **must stay stable**
across restarts/restores (PLAN.md:240). Leave `GB_API_KEY` blank for now.

### 2. Bring up the stack

```bash
docker compose up -d
```

Three services come up on the compose network: `growthbook` (UI :3000, API :3100),
`mongo` (:27017), `postgres` (:5432). Wait for `curl -s localhost:3100/healthcheck` to
return `healthy: true`.

### 3. Generate + load the toy dataset

```bash
uv run python -m phase0.generate
```

Deterministically builds 5k users, one experiment's worth of exposures, and an `orders`
event table, then loads `identifies` / `experiment_viewed` / `orders` into Postgres. Do
this **before** creating the data source so GrowthBook's connection test has rows to read.

### 4. First-run GrowthBook setup (UI, one-time)

1. Open <http://localhost:3000> and create the first account (company, name, email,
   password). This is the org admin.
2. Go to **Settings → API Keys → New Secret Key**, role **Admin**, Reveal it, and paste
   it into `.env` as `GB_API_KEY`.

### 5. Create the Postgres data source (UI, one-time) — the captured artifact

**Metrics and Data → Data Sources → Add Data Source → Postgres → Segment**, then:

| Field | Value |
|---|---|
| Name | `Warehouse (Postgres)` |
| Projects | *(remove "My First Project" so it's available to all projects)* |
| Host | `postgres` |
| Port | `5432` |
| Database | `warehouse` |
| User / Password | `gbsynth` / `gbsynth` |
| Default Schema | `public` |

Keep the Segment default exposure table (`experiment_viewed`). You should see
**"Connection successful!"**. Finish.

> **Finding — Segment auto-config needs `context_*` columns.** GrowthBook's Segment schema
> auto-generates two assignment queries (`anonymous_id` + `user_id`) that select
> `context_campaign_source`, `context_campaign_medium`, and `context_user_agent` to derive
> the source/medium/device/browser dimensions. The generator already emits these columns,
> so the queries run. If you ever trim the schema, the snapshot will fail on missing
> columns. (Discovered by capturing the data-source doc — next step.)

### 6. Capture the data-source document (bootstrap template)

```bash
uv run python -m phase0.capture_datasource
```

Writes `phase0/captured_datasource.json` (gitignored — it holds the `ENCRYPTION_KEY`-bound
encrypted `params`). This is the template Phase 2's `bootstrap.py` replays to seed data
sources directly in Mongo, closing the one REST-API gap (data sources are read-only via
the API, PLAN.md:52-56).

### 7. Provision + verify

```bash
uv run python -m phase0.provision
```

Auto-discovers the data source + its `user_id` assignment query, then creates the project,
the orders fact table, two fact metrics (proportion + mean), and the experiment with a
backdated running phase. Triggers a snapshot, polls it, reads the results, and asserts no
SRM warning — printing the computed lift. Idempotent: safe to re-run.

Confirm in the UI under the experiment's **Results** and **Health** tabs.

### 8. Validate the reset loop

```bash
uv run python -m phase0.reset snapshot golden     # golden dump of clean state
# ...make a mess (create a junk flag in the UI, edit something)...
uv run python -m phase0.reset restore golden      # revert; mess is wiped
uv run python -m phase0.reset list
```

A restore logs out active sessions and discards post-snapshot changes (PLAN.md:210) —
fine off-hours, never mid-demo.

## Teardown

```bash
docker compose down        # keep volumes (data persists)
docker compose down -v     # wipe everything (fresh start next time)
```

## Findings worth carrying forward

Integration facts this spike surfaced (the whole point of Phase 0):

1. **Segment auto-config needs `context_*` columns** — see step 5. The exposure tables
   must carry `context_campaign_source/medium` + `context_user_agent`.
2. **`NUMERIC` warehouse columns are detected as `string`.** The Postgres driver returns
   `NUMERIC`/`DECIMAL` as strings, so GrowthBook types them as string and rejects `sum`
   aggregation on mean/ratio metrics. **Fix:** `CAST(col AS double precision)` in the
   fact-table SQL (the warehouse column can stay `NUMERIC`). Snapshots compute correctly
   either way (aggregation happens in-warehouse); only metric-definition validation trips.
3. **Experiments have no REST `DELETE`.** `POST /experiments`, `/start`, `/stop`, and
   updates exist, but no delete — provisioning must reuse-by-tracking-key (which it does),
   and full teardown of an experiment needs Mongo (i.e. the reset path), not the API.
4. **Two assignment queries** are auto-created (`anonymous_id` + `user_id`); pick the one
   matching your metrics' identifier to avoid an unnecessary identity join.

## What graduates to Phase 1

The spike is deliberately self-contained under `phase0/`. Proven patterns that move into
the real `src/gbsynth/` package next:

- **Hash-based deterministic assignment** + within-window conversions → the core
  `experiments.py` / `sessions.py` engines.
- **The Segment table shape** (incl. `context_*` columns) → `schemas/segment.py`.
- **`GBClient`** (auth, retries) → `provision/client.py`, with the full 60 req/min throttle.
- **`reset.py`** mongodump/mongorestore → `reset.py` with dump hygiene + versioned retention.
- **`captured_datasource.json`** → the concrete template for `provision/bootstrap.py`.

Deferred (not needed to validate the core assumption): CUPED latent propensities, the
story-solver, ClickHouse, refresh/rotation, Fly deployment.
