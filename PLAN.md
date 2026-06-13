# GrowthBook Synthetic Demo Data — Research & Build Plan

**Goal:** A generator that produces realistic synthetic feature-flag, experiment, and
product-analytics data for four verticals (e-com, B2B, SaaS, fintech), loads it into a
warehouse (Postgres + ClickHouse), and provisions a complete GrowthBook demo workspace on
top of it — so GrowthBook's own stats engine computes believable experiment results for
sales/marketing demos.

## Locked decisions

| Decision | Choice |
|---|---|
| Warehouse targets | Postgres + ClickHouse |
| Scope | Full turnkey: warehouse data + GrowthBook objects via REST API |
| Event schema | Per-vertical mix (see Schema strategy) |
| Freshness | Rolling/refreshable — demos always show recent dates and running experiments |
| Org layout | One workspace, GrowthBook Projects per vertical |
| Scale | Mid-size: ~100k–500k users, low millions of events, ~12 months history per vertical |
| Demo stories | 3–5 hand-scripted flagship experiments per vertical + procedural backfill |
| Stack | Python (numpy/faker for generation, psycopg + clickhouse-connect for loading) |
| Tooling | Astral stack: `uv` (project/deps/venv, `uv run` everywhere), `ruff` (lint + format), `ty` (type checking); pytest for tests |
| Hosting | **Self-hosted GrowthBook on Fly.io** (internal enterprise license); resets via Mongo snapshot/restore |

## Ground-truth research findings (verified against GrowthBook source, mid-2026)

Verified against the local clone at `~/projects/growthbook` (OpenAPI spec
`packages/back-end/generated/spec.yaml` + API routers/validators).

### What the REST API can provision

Everything we need **except the data source**:

- **Projects, environments, attributes, saved groups, dimensions, segments** — full CRUD.
- **Fact tables + filters + fact metrics** — full CRUD, plus `POST /v1/bulk-import/facts`
  which upserts fact tables, filters, and metrics in one call (resources get
  `managedBy: api`). Ideal for our provisioner.
- **Feature flags** — use the v2 API (`POST /v2/features`): per-env enabled state and a
  top-level `rules` array supporting `force`, `rollout`, and `experiment-ref` rules with
  conditions, saved-group targeting, prerequisites, and schedules.
- **Experiments** — `POST /v1/experiments` with `trackingKey`, variations, datasource +
  assignment query, metrics, and **backdated phases** (`phases[].dateStarted/dateEnded`
  accept any past ISO datetime; `status` settable to draft/running/stopped at creation).
  Update endpoint sets `results` (won/lost/dnf/inconclusive), `winner`, and `analysis`
  narrative text. `/start` and `/stop` lifecycle endpoints exist.
- **Snapshots** — `POST /v1/experiments/{id}/snapshot` triggers an async results refresh;
  poll `GET /v1/snapshots/{id}`. Results are *always* computed from the warehouse — they
  cannot be injected. This is why the synthetic data must be statistically engineered.
- **SDK connections** — full CRUD (some options premium-gated: encryption, remote eval).

**The one gap: data sources are read-only via the API** (list/get only — verified in
`data-sources.router.ts`). The Postgres/ClickHouse connection, its identifier types, and
its experiment-assignment queries cannot be created via REST. **Self-hosting closes this
gap:** with direct Mongo access we seed the data-source document ourselves
(`bootstrap.py`), making the entire org bootstrappable from zero with one command. The
generator still takes `datasourceId` + `assignmentQueryId` as config inputs so it can
also run against any pre-configured org.

Other operational facts: auth via secret key (Bearer), **60 req/min rate limit** (the
provisioner needs throttling/backoff), no official management-API client (we'll generate
a typed Python client from the OpenAPI spec, or just write a thin `httpx` wrapper),
premium-gated experiment fields to avoid on non-enterprise orgs (`metricOverrides`,
`postStratificationEnabled`, decision framework settings).

### What the warehouse data must look like

- **Assignment/exposure table** — the heart of it. Columns: identifier(s)
  (`user_id` and/or `anonymous_id`), `timestamp`, `experiment_id` (= the experiment's
  *tracking key*), `variation_id` (0-based index as string/int). Extra columns
  (`device`, `browser`, `country`, `source`) become experiment dimensions. Multiple
  exposure rows per user are realistic and safe; GrowthBook dedupes to first exposure.
- **Fact tables** — flat event tables, each with identifier column(s), `timestamp`,
  numeric value columns (for mean/ratio/quantile metrics), and low-cardinality string
  columns (for filters/dimensions). Fact metric types: proportion, retention, mean,
  ratio (cross-fact-table), quantile — all supported on both Postgres and ClickHouse.
- **Identifier join table** — an `identifies`-style table mapping
  `user_id ↔ anonymous_id` so metrics on either identifier type resolve.
- **Schema flavors:** Segment and Rudderstack auto-configuration expects
  `experiment_viewed` + `identifies` tables and "just works." **GA4's native integration
  is BigQuery-only** (nested `event_params`, wildcard tables) — so the e-com "GA4 flavor"
  will be a *flattened* GA4-style events table under a Custom-schema data source, not the
  native GA4 option.
- **ClickHouse specifics:** GrowthBook connects over HTTP(S) :8123/:8443, not native TCP.
  Use `DateTime64` timestamps, MergeTree `ORDER BY (user_id, timestamp)`, consistent
  string id types across tables, avoid `Nullable` where possible.

### Realism constraints (what makes results look real — or fake)

1. **SRM:** chi-squared test on first-exposure counts vs configured weights; warning at
   p < 0.001. → Assign variations by true independent randomization (hash of
   user_id + experiment salt), never post-hoc.
2. **Multiple exposures:** warning when >~1% of users appear in 2+ variations. →
   variation must be deterministic per (user, experiment).
3. **Conversion windows:** default windows (~72h) mean conversion events must land
   shortly after each user's exposure timestamp, not uniformly across the experiment.
4. **CUPED:** only visibly tightens intervals if pre-exposure behavior correlates with
   post-exposure behavior per user. → Give each user a stable latent "propensity" that
   drives both pre- and post-period activity.
5. **Effect sizes:** believable lifts are ~2–10%; with mid-size samples that yields
   chance-to-beat-control of ~85–99% for winners, ~50% for flat metrics. Revenue should
   be lognormal so intervals look organically noisy. Include a mild guardrail regression
   in at least one story experiment.
6. **Seasonality:** daily/weekly traffic cycles + gradual growth so "users over time"
   charts look organic; dimension splits balanced across variations.

## Architecture

```
growthbook-synthetic-data/
├── config/
│   ├── workspace.yaml          # GB API host/key, datasourceId, assignmentQueryIds, project ids
│   └── verticals/              # one spec per vertical (declarative)
│       ├── ecom.yaml           #   personas, funnels, events, metrics, flags, experiments
│       ├── saas.yaml
│       ├── b2b.yaml
│       └── fintech.yaml
├── src/gbsynth/
│   ├── core/
│   │   ├── population.py       # user generation: personas, latent propensities, traits, signup curves
│   │   ├── sessions.py         # session/event simulation: funnels, seasonality, device/geo mixes
│   │   ├── experiments.py      # assignment engine: deterministic hashing, exposure events,
│   │   │                       #   effect application (multiplicative lift on funnel probs/values)
│   │   └── stories.py          # scripted-outcome engine: solves for per-variation params that
│   │                           #   hit a target chance-to-win / p-value at the planned sample size
│   ├── schemas/                # output table shapes per flavor
│   │   ├── segment.py          # experiment_viewed, identifies, tracks/pages + per-event tables
│   │   ├── ga4_flat.py         # flattened GA4-style events table (Custom data source)
│   │   └── common.py           # users, accounts (B2B), orders, transactions, subscriptions
│   ├── load/
│   │   ├── postgres.py         # DDL + COPY-based bulk load
│   │   ├── clickhouse.py       # DDL + HTTP insert (clickhouse-connect)
│   │   └── files.py            # CSV/Parquet export (escape hatch for any other warehouse)
│   ├── provision/
│   │   ├── client.py           # thin typed wrapper over GB REST API (httpx, 60rpm throttle, retries)
│   │   ├── workspace.py        # projects, environments, attributes, saved groups, SDK connections
│   │   ├── metrics.py          # fact tables/filters/metrics via bulk-import
│   │   ├── features.py         # flags + rules (v2 API)
│   │   ├── experiments.py      # experiments w/ backdated phases, stop+results for finished ones,
│   │   │                       #   snapshot trigger + poll, verify outcome matches the script
│   │   └── bootstrap.py        # seed the data-source document directly in Mongo (closes the
│   │                           #   one API gap → zero-touch org bootstrap; self-hosted only)
│   ├── reset.py                # golden snapshots: mongodump after each verified refresh;
│   │                           #   mongorestore to revert the org (see Reset section)
│   └── cli.py                  # gbsynth generate / load / provision / refresh / snapshot / reset / verify
├── pyproject.toml              # uv-managed project; ruff + ty config; [project.scripts] gbsynth
├── uv.lock
├── PLAN.md
└── README.md                   # runbook for sales/marketing ops
```

### Python tooling conventions

- **uv** owns everything: `uv init` / `uv add` for deps, `uv run gbsynth ...` for
  execution, `uv.lock` committed for reproducible installs, `uv python pin` for the
  interpreter version. No pip/poetry/requirements.txt anywhere; CI and the refresh cron
  both run via `uv run` (or `uv sync --locked` + the venv).
- **ruff** for both linting and formatting (replaces black/isort/flake8), configured in
  `pyproject.toml`.
- **ty** (Astral's type checker) for type checking; the codebase is fully typed —
  especially the vertical-spec models (pydantic) and the API client.
- **pytest** via `uv run pytest`; the story-solver gets statistical regression tests
  (generated data → gbstats → asserted outcome ranges).

### Key design points

- **Declarative vertical specs.** Each vertical is a YAML spec: personas with base
  conversion/engagement rates, an event taxonomy, funnel definitions, metric definitions
  (mapped to fact metrics), the flag catalog, and the experiment roster (scripted stories
  with target outcomes + procedural backfill). The Python core is vertical-agnostic;
  adding a vertical = writing a spec.
- **Deterministic generation.** Everything keyed off a seed + stable hashing
  (user_id → variation, user_id → propensity). Re-running with the same seed reproduces
  the dataset; the refresh job extends it without rewriting history.
- **Scripted stories via simulation-solving.** For each story experiment, `stories.py`
  takes a target ("checkout redesign: +6% conversion, ~97% chance to win, AOV flat,
  page-load guardrail slightly worse") and computes the per-variation parameter deltas
  that achieve it in expectation at the planned sample size, then runs one verification
  pass with GrowthBook's actual math (or `gbstats` directly, which is open source) before
  loading. Procedural backfill experiments draw effects from a realistic prior
  (most flat/small, occasional winner/loser).
- **Refresh model.** A `refresh` command (cron-able) that: advances the clock — generates
  yesterday's users/events/exposures for ongoing experiments and evergreen flags,
  appends to both warehouses, and triggers experiment snapshots via the API so results
  in the UI are current. Story experiments have planned lifecycles (e.g., a "running"
  story is always 2–3 weeks into its phase; when it completes, the refresher stops it
  with results via API and starts the next one from a rotation) so the workspace
  *always* has live, mid-flight experiments to demo.

### Reset to golden state (`gbsynth snapshot` / `gbsynth reset`)

Requirement: AEs and marketers will create flags, experiments, metrics, etc. during
demos; the workspace must be revertible to a clean state.

Two facts make this simple on a self-hosted instance:
- **Demo-created junk only lives in GrowthBook's Mongo database, never in the
  warehouse** — GrowthBook's warehouse credentials are read-only, so the synthetic data
  can't be corrupted by demo activity.
- **With Mongo access, reset is snapshot/restore** — atomic, seconds, and *complete*. It
  reverts everything API reconciliation can't fully reach: org settings, archived
  experiments, draft revisions, member-role changes, audit noise.

Mechanics:
1. **Golden snapshots.** After each successful nightly `refresh` + `verify`, `gbsynth
   snapshot` runs `mongodump` of the GrowthBook database and stores it versioned
   (Tigris/S3 bucket; keep the last ~14). Re-snapshotting nightly matters because the
   refresh advances experiment lifecycles — restoring an old dump would roll the
   rotation back out of sync with the warehouse.
2. **Reset.** `gbsynth reset` = `mongorestore --drop` of the latest golden dump. Runs
   nightly after the snapshot step's predecessor check (so each day starts clean) and
   on-demand before important demos.
3. **Caveats:** a restore logs out active sessions and discards in-flight edits — fine
   off-hours, just don't run it mid-demo. Team-roster changes made after the last golden
   dump roll back too; take a fresh snapshot after intentional admin changes.
4. **Sandbox project.** A fifth project, "Sandbox", where AEs are told to do free-form
   creation during demos. With full restores it's not load-bearing — it's a hygiene
   convention that keeps the four golden vertical projects clean *between* resets.

Deliberate choice: golden fact tables/metrics from `bulk-import` get `managedBy: api`,
which locks them in the UI — AEs can't break metric definitions mid-demo. Flags and
experiments are left UI-editable (demos need to show editing flows); the nightly reset
absorbs the drift.

### Durability & failure-mode requirements

These are load-bearing properties, not nice-to-haves:

- **Refresh is idempotent and date-partitioned.** Re-running a day's refresh must not
  duplicate rows: generation is deterministic per (seed, date), and loads are
  delete-then-insert by date partition. A `_gbsynth_meta` watermark table **in each
  warehouse** records what's been generated/loaded.
- **The generator holds no private state.** Everything it needs is either derivable from
  the seed (user propensities, variation assignments) or read from the warehouse
  watermark / GrowthBook API. Nothing stateful lives in Mongo (nightly resets wipe it)
  or on the CI runner (ephemeral). This is what makes crashed runs safely re-runnable.
- **Nightly order and failure semantics:** reset (to last golden) → refresh → verify →
  snapshot (new golden). If verify fails, **alert and skip the snapshot** — the system
  self-heals to the last known-good state on the next reset, and stale-by-one-day is
  invisible in a demo. Warehouse appends from a failed run are harmless: queries are
  bounded by experiment phase dates, and the idempotent loader reconciles the partition
  on the next run.
- **`ENCRYPTION_KEY` is sacred.** GrowthBook encrypts data-source credentials in Mongo
  with it; `bootstrap.py` must encrypt seeded credentials the same way, and the key must
  remain stable across restores and redeploys (store in Fly secrets; never rotate
  casually — rotation requires re-seeding data sources and a fresh golden dump).
- **Upgrade runbook:** golden snapshot → bump pinned image → app runs its Mongo
  migrations on boot → `gbsynth verify` → take a *fresh* golden snapshot immediately
  (so future restores don't repeatedly re-trigger migrations). On failure: roll back
  the image and restore the pre-upgrade dump.
- **Retention:** the rolling refresh appends forever; a monthly prune drops warehouse
  partitions older than ~13 months and ages out fully-stopped backfill experiments, so
  size and history depth stay constant.
- **Dump hygiene:** exclude GrowthBook's query-cache/results collections from
  `mongodump` (they're large and recomputable via snapshot triggers) to keep golden
  dumps small and restores fast.
- **Reset pause switch:** a one-flag override (workflow input / Fly secret) to skip
  tonight's reset, for when an AE has staged something for a morning demo.

### Per-vertical sketch (to be detailed in specs during build)

| | E-com | SaaS | B2B | Fintech |
|---|---|---|---|---|
| Schema flavor | GA4-flavored flat (Custom) | Segment | Segment/Rudderstack | Custom flat |
| Core entities | sessions, products, orders | users, subscriptions, feature usage | accounts + seats, opportunities, usage | users, accounts, transactions, KYC funnel |
| Example metrics | conversion rate, AOV, revenue/user, cart abandonment, P95 page load | signup→activation, trial→paid, WAU retention, expansion MRR | demo requests, seat activation, account-level usage depth | application completion, funding rate, txn volume, fraud-flag rate (guardrail) |
| Story experiment examples | checkout redesign (win), free-shipping threshold (AOV trade-off), PDP gallery (flat) | onboarding checklist (win), pricing page (lose on a surprise), paywall timing (running) | self-serve trial flow (win), demo-form length (guardrail save) | simplified KYC (win + fraud guardrail save), card-activation nudge (running) |
| Flags | ~15–25 per vertical: mix of kill switches, ramps, targeting rules, experiment-refs, some stale (for cleanup demos) | same | same | same |

## Build phases

**Phase 0 — Validation spike (small, de-risks everything)**
Stand up the full stack locally via docker compose (growthbook + mongo + postgres).
Hand-write one tiny dataset (1 experiment, 2 metrics, 5k users), create the data source
in the UI (then capture its Mongo document as the `bootstrap.py` template), provision
via API script, trigger a snapshot, and confirm the UI shows the intended result with no
SRM/exposure warnings. Also validate the reset loop: mongodump → make UI mess →
mongorestore → confirm clean. This validates every integration assumption (incl. exact
auto-generated Segment SQL and identifier-type behavior) before real investment.
*Deliverable: a working end-to-end "hello world" demo experiment + proven reset.*

**Phase 1 — Core engine + SaaS vertical on Postgres**
Population/session/experiment engines, the SaaS spec (most familiar territory for GB
demos), Postgres loader, story-solver with verification against `gbstats`.
*Deliverable: full SaaS dataset loading into Postgres with verified story outcomes.*

**Phase 2 — GrowthBook provisioner + Fly deployment**
API client + workspace/metrics/features/experiments provisioning, idempotent re-runs
(upsert semantics, `managedBy: api`), snapshot trigger + outcome verification,
`bootstrap.py` (Mongo data-source seeding incl. credential encryption). Stand up the
Fly.io stack now — app, Mongo, Postgres — so sales gets a usable SaaS-only workspace
early and hosting issues surface before they're load-bearing.
*Deliverable: one command builds the complete SaaS project in the live demo org.*

**Phase 3 — Remaining verticals + ClickHouse**
E-com, B2B, fintech specs; ClickHouse loader; per-vertical schema flavors.
*Deliverable: all four projects live in the workspace, on either warehouse.*

**Phase 4 — Rolling freshness + reset automation**
The `refresh` command, experiment lifecycle rotation, `snapshot`/`reset` (mongodump/
mongorestore wrappers), the Tigris dump bucket, and the GitHub Actions cron (nightly:
reset → refresh → verify → snapshot) with failure alerting, plus the `verify` command
that audits the workspace (results match scripts, no SRM/exposure warnings, dates
current). *Deliverable: a production demo workspace that never looks stale and survives
any demo.*

**Phase 5 — Polish + sales enablement**
Demo-story runbook per vertical (what to click, what the narrative is), README for
regenerating/reseeding, cleanup command to tear down and rebuild a project, retention
pruning, reset pause switch.

**Backlog / future**
- Live demo apps: a small sample app per vertical (e.g., a storefront) consuming the SDK
  via the provisioned SDK connections, so demos can show flags evaluating live — the
  one demo moment pure data can't provide.
- GrowthBook managed-warehouse ingestion path (see Hosting decision 5).
- Ephemeral per-demo environments (spin up app+Mongo, restore golden dump).

**Scope lever:** if timeline slips, the per-vertical schema mix is the thing to cut —
ship every vertical on the Segment shape first (auto-configures, one schema module) and
add the GA4-flavored e-com and custom fintech schemas as a fast-follow. The vertical
*content* (metrics, stories) carries the demo value; the schema flavor is mostly
backstage realism.

## Hosting & operations decisions

1. **Demo org: self-hosted GrowthBook on Fly.io** (internal enterprise license).
   Rationale vs Cloud: Mongo access gives *perfect* snapshot/restore resets and
   Mongo-seeded data sources (zero-touch bootstrap), the self-hosted enterprise app is
   visually identical to Cloud for prospects, and an enterprise license shows the
   premium features that close deals — CUPED, sequential testing, approvals, safe
   rollouts. Tradeoffs accepted: we own upgrades (pin the official Docker image to a
   version tag; deliberate weekly bump via `fly deploy --image growthbook/growthbook:vX.Y`,
   never auto-latest and never right before a marquee demo), and Cloud-only features
   (managed warehouse, built-in event tracking) get demoed from a separate vanilla
   Cloud org when needed.
2. **Topology: everything on Fly's private network (6PN), one `demo` Fly org.**
   - `growthbook-app`: official image, front-end :3000 + API :3100 behind Fly HTTPS on
     a custom domain (e.g. `demo.growthbook.io`); `APP_ORIGIN`/`API_HOST` set accordingly.
   - `growthbook-mongo`: Mongo with a Fly volume, **private-only** (no public IP); Fly's
     automatic volume snapshots as belt-and-braces under our golden dumps.
   - `demo-postgres`: Fly Managed Postgres (or a small Postgres app), private-only.
   - `demo-clickhouse`: single-node ClickHouse (official image + volume), private-only;
     GrowthBook connects over HTTP :8123 on the internal network.
   - Golden dumps in a Tigris (Fly-native S3) bucket.
3. **Cron: GitHub Actions scheduled workflow** in this repo (nightly: reset → refresh →
   verify → snapshot). Reaches private Fly services via `flyctl proxy` (token in Actions
   secrets); run history visible to the team. Escape hatch: a tiny Fly scheduled machine
   if Actions runtime/networking ever becomes annoying.
4. **One shared demo org**, not per-AE clones. The nightly reset solves the shared-org
   mess problem that per-AE clones would otherwise exist to solve, at 1/Nth the
   maintenance. If a marquee demo needs guaranteed-pristine state, run `gbsynth reset`
   on demand first. Self-hosting also unlocks ephemeral per-demo environments later
   (spin up app+Mongo, restore golden dump, point at the shared warehouses).
5. **GrowthBook managed warehouse: defer to backlog.** Direct Postgres/ClickHouse
   inserts are simpler and fully under our control. If demos later need the managed
   warehouse story, add an ingestion path through GB's event endpoint as a v2 loader.
