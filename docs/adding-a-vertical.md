# Adding a vertical

The generation engine is vertical-agnostic: every vertical's funnel reduces to a **binary
conversion** plus a **per-user value**, so adding one is a YAML spec in
`config/verticals/<name>.yaml` — no code. This guide is also the spec-field reference.

## The four steps

```bash
cp config/verticals/saas.yaml config/verticals/mything.yaml   # 1. copy a spec
$EDITOR config/verticals/mything.yaml                          # 2. edit (see reference below)
uv run gbsynth generate mything                                # 3. verify stories via gbstats (no DB)
uv run gbsynth provision mything                               # 4. build it live (--warehouse clickhouse optional)
```

Step 3 is the tight loop: it builds the dataset and asserts every decisive story lands in
band against `gbstats`, with no DB or GrowthBook needed. Iterate there until it PASSes, then
provision. The parametrized test in `tests/test_stories.py` then covers your vertical
automatically.

## Spec reference

```yaml
name: mything          # used for the project, warehouse DB, and id namespacing
seed: 42               # deterministic: same seed reproduces the dataset exactly
schema_flavor: segment # only segment today

scale:
  n_users: 40000       # population over the history window
  months: 12           # months of history (signups span this, ending today)
```

### personas (the population mix)
`weight` is relative (normalised). Each user is one persona + a stable latent propensity
(~1) that scales their behaviour — the hook CUPED needs later.

```yaml
personas:
  - name: smb
    weight: 0.5
    conversion_base: 0.34   # P(convert) before propensity & experiment effect (0..1)
    value_log_mean: 3.4     # lognormal params for the value a converting user contributes
    value_log_std: 0.5      #   (revenue / MRR / ACV / savings); median ≈ exp(value_log_mean)
```

### metrics (exactly one proportion + optionally one mean)
The **proportion** metric is the conversion the experiment effect is applied to. The
**mean** metric is the per-user value; it rises only because more users convert (organic
correlation). `event` is the row's event name in the generated `tracks` table.

```yaml
metrics:
  - {key: signup,  name: Signup conversion, type: proportion, event: converted}
  - {key: revenue, name: Revenue per user,  type: mean, event: purchase, value_column: value}
```

### stories (the scripted experiments)
The **target chance-to-win encodes the story type** and drives the solver, which finds the
lift that lands it at the real sample size:

| Story type | `target_chance_to_win` | Asserted as |
|---|---|---|
| Win | `0.97` (or `0.99` for low-base verticals) | chance-to-win ≥ 0.80 |
| Surprise loss | `0.04` (or `0.02`) | chance-to-win ≤ 0.20 |
| Flat / running | `0.50` | not asserted (legitimately noisy) |

Each story exposes a **disjoint signup cohort** via its window, so no user is in two
experiments. The window is `[now − ends_days_ago − phase_days, now − ends_days_ago)`:
- `ends_days_ago: 0` → **running** (the always-live demo experiment); keep exactly one.
- `ends_days_ago > 0` → **stopped** that many days ago (backdated, gets results set).
- **Keep windows non-overlapping.** The convention used by all specs: running `[40,0]`,
  win `ends 45 / phase 55` → `[100,45]`, loss `ends 105 / phase 55` → `[160,105]`, backfill
  from 160 back.

```yaml
stories:
  - key: mything-onboarding      # tracking key == experiment_id; namespace with the vertical
    name: Onboarding checklist
    ends_days_ago: 45
    phase_days: 55
    primary_metric: signup       # must be the proportion metric's key
    target_chance_to_win: 0.97
    variations:
      - {key: "0", name: Control}
      - {key: "1", name: Treatment}
```

### backfill (procedural history, optional)
N small experiments with effects drawn from a prior (mostly flat, the occasional
significant), in disjoint windows behind the hand-scripted stories. Not asserted.

```yaml
backfill: {count: 5, phase_days: 30, starts_days_ago: 160, lift_std: 0.05, primary_metric: signup}
```

### flags (custom feature flags, optional)
Beyond the auto-generated catalog (kill switches, rollout, targeting, stale, and an
experiment-ref per story), a vertical can declare custom flags — e.g. JSON config flags.
`default_value` is a string (JSON-serialized for `value_type: json`).

```yaml
flags:
  - id: mything-model-config
    description: Model + decoding params.
    value_type: json          # boolean | string | number | json
    default_value: '{"model":"claude-sonnet-4-5","temperature":0.2}'
    tags: [config]
```

## Tuning tips

- **Low base rate ⇒ high variance.** A ~5% conversion vertical needs either more `n_users`
  or more decisive targets (`0.99` / `0.02`) so a single realization clears the band — see
  `ecom.yaml`. High-base verticals (≥30%) are reliable at modest scale.
- **The solver does the work.** You give the target chance-to-win; it finds the lift. You
  rarely set `target_lift` directly (it exists as an override).
- **Determinism:** everything keys off `seed` + `now`. `generate` uses today's `now`;
  `provision`/`refresh` build + load + verify with a single `now` so live results match.
