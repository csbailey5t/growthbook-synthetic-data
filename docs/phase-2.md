# Phase 2 — GrowthBook provisioner (Fly-independent slice)

`gbsynth provision saas` builds the complete SaaS project in a running GrowthBook from the
Phase 1 dataset — data source, metrics, and experiments-with-results — and verifies each
live snapshot reproduces the gbstats-predicted outcome. It runs against **any** reachable
GrowthBook + Mongo; the local docker-compose stack is the dev target. The Fly deployment is
a separate, later concern — nothing here depends on it.

## What it does (one idempotent command)

1. **Build + load** the dataset into Postgres (shared `now` with verification).
2. **Bootstrap the data source** directly in Mongo (`provision/bootstrap.py`) — data sources
   are read-only via the REST API (PLAN.md:52-56), so the document is seeded with
   credentials encrypted under `ENCRYPTION_KEY`. Idempotent by name.
3. **Project** (`workspace.py`), **metrics** via `bulk-import/facts` (`metrics.py`,
   `managedBy: api`).
4. **Experiments** (`experiments.py`): backdated phases; stopped stories get
   results/winner/analysis; snapshot + poll; verify live chance-to-win vs the gbstats
   prediction (decisive outcomes asserted; flat ones are noise-sensitive and reported only).

## Run

```bash
docker compose up -d                       # local GrowthBook + Mongo + Postgres
# .env must have GB_API_KEY (admin) + ENCRYPTION_KEY matching the running app
uv run gbsynth provision saas
```

Verified live (local GrowthBook 4.4.0): onboarding **Won** (live CTW 99.0% vs predicted
99.1%), pricing **Lost** (17.0% vs 16.8%), paywall **running** (79.6%), 5 backfill
experiments — all SRM-quiet. Screenshot: `docs/screenshots/phase2-onboarding-won.png`.

## Key findings

- **Credential encryption** (`provision/crypto.py`): GrowthBook uses crypto-js
  `AES.encrypt(json, ENCRYPTION_KEY)` — OpenSSL passphrase mode (MD5 EVP_BytesToKey, random
  salt, AES-256-CBC, `Salted__` base64). Reproduced in Python and validated against a real
  GrowthBook blob; this is what makes zero-touch Mongo seeding work.
- **Results CTW** lives on the *treatment* variation (`variations[1]`); the baseline's
  `chanceToBeatControl` is `0`, not null.
- **Decisive-only verification**: near-0.5 predictions swing widely on tiny count
  differences, so only decisive (CTW ≥0.9 / ≤0.1) outcomes are asserted live — the same
  principle as Phase 1's bands. SRM is always asserted quiet.

## Deferred follow-ups

Feature-flag catalog + `features.py`; SDK connections; environments/attributes/saved-groups;
graduating `reset.py`/`snapshot` into the package; and the actual Fly deployment (blocked on
credentials — the provisioner already targets a configurable host, so it's a config change).
