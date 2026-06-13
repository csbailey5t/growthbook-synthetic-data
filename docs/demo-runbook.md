# Demo runbook (sales / marketing ops)

How to stand up, refresh, and demo the synthetic GrowthBook workspace. Everything runs
against the local docker-compose stack today; the same commands target a hosted GrowthBook
once `GB_API_HOST`/credentials point there.

## One-time setup

```bash
docker compose up -d                 # growthbook + mongo + postgres + clickhouse
cp .env.example .env                 # set JWT_SECRET + ENCRYPTION_KEY (openssl rand -hex 32)
uv sync
```

Then in the GrowthBook UI (http://localhost:3000): create the first admin account, and
Settings → API Keys → create an **admin secret key**; paste it into `.env` as `GB_API_KEY`.

## Build the workspace

```bash
uv run gbsynth provision saas        # Postgres-backed
uv run gbsynth provision ecom
uv run gbsynth provision b2b
uv run gbsynth provision fintech
uv run gbsynth provision ai
# any vertical can run on ClickHouse instead:
uv run gbsynth provision fintech --warehouse clickhouse
```

Each builds a project, a data source (seeded directly — no UI clicks), metrics,
experiments (with backdated phases + results), and a feature-flag catalog.

## Daily / pre-demo operations

```bash
uv run gbsynth refresh saas          # advance data + results to today (run per vertical)
uv run gbsynth verify saas           # confirm demo-ready: no SRM, decisive stories decisive
uv run gbsynth snapshot golden       # capture a clean golden state
uv run gbsynth reset golden          # revert the org after a demo (wipes AE edits)
uv run gbsynth cleanup ai            # tear down one vertical to rebuild it fresh
```

## What to show, per vertical

Each vertical has a **live (running)** experiment, a **win**, and a **surprise loss**, plus
procedural backfill history. Open the project → Experiments.

| Vertical | Win (shipped) | Surprise loss (caught) | Running (mid-flight) |
|---|---|---|---|
| **saas** | Onboarding checklist lifts activation | Pricing page redesign hurt activation | Paywall timing (inconclusive) |
| **ecom** | One-page checkout lifts purchases | Sitewide promo banner distracted from checkout | PDP image gallery |
| **b2b** | Self-serve trial lifts demo→opportunity | Gated pricing page hurt intent | Shorter demo form |
| **fintech** | Simplified KYC lifts funding | Upfront fee disclosure caused sticker shock | Card activation nudge |
| **ai** | Claude Sonnet 4.5 beats GPT-4o on deflection | Eager auto-resolve prompt hurt deflection | RAG context window 8k vs 16k |

Talking points that always hold (engineered into the data):
- **Health tab is clean** — no SRM, no multiple-exposure warnings (true randomization).
- **Believable lifts** — winners land ~2–10% lift at ~97% chance-to-win, not a fake 100%.
- **Two correlated metrics** — the conversion metric and a value metric (revenue/MRR/ACV/
  automation savings) both move, the value via the conversion.
- **Backfill history** — a realistic scatter of mostly-flat past experiments.

### AI vertical — the flag-driven story
The AI copilot's model/prompt config ships as **feature flags**: `ai-model-config` (JSON:
model + temperature + max tokens), `ai-rag-settings` (JSON: top-k, rerank, context tokens),
`ai-system-prompt-version` (string). The model-swap and prompt experiments are linked to
experiment-ref flags, so the demo shows "we A/B test model swaps and prompt changes, gated
by config flags, and GrowthBook measures deflection rate."

## How the realism holds up
Outcomes are engineered and **verified offline against `gbstats`** (the exact engine
GrowthBook runs) before loading, and re-verified live after provisioning — so the numbers
on the results page are the numbers we intended, with no SRM warnings.
