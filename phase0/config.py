"""Shared config + .env loader for the Phase 0 validation spike.

No python-dotenv dependency — a tiny parser keeps the spike lean. All knobs for the
toy dataset live here so generate.py and provision.py agree on names and the experiment
story.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> dict[str, str]:
    """Parse the repo .env into a dict. KEY=VALUE lines, ignores blanks/comments."""
    env_path = path or (REPO_ROOT / ".env")
    env: dict[str, str] = {}
    if not env_path.exists():
        raise FileNotFoundError(f"{env_path} not found — copy .env.example to .env first.")
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


ENV = load_env()

# --- Warehouse (Postgres, exposed on the host by docker-compose) ---
PG_DB = ENV["POSTGRES_DB"]
PG_DSN = (
    f"host=localhost port=5432 dbname={PG_DB} "
    f"user={ENV['POSTGRES_USER']} password={ENV['POSTGRES_PASSWORD']}"
)

# --- GrowthBook REST API ---
GB_API_HOST = ENV.get("GB_API_HOST", "http://localhost:3100")
GB_API_KEY = ENV.get("GB_API_KEY", "")

# --- The toy "hello world" experiment story -------------------------------------------
# Deterministic: same SEED reproduces the dataset exactly. Variation is assigned by a
# stable hash of (user_id, salt) — true independent randomization, which is what keeps
# GrowthBook's SRM check happy (PLAN.md:88-92).
SEED = 42
N_USERS = 5_000

PROJECT_NAME = "Phase 0 Spike"
EXPERIMENT_NAME = "Checkout redesign (Phase 0)"
EXPERIMENT_KEY = "phase0-checkout-redesign"  # tracking key == experiment_id in warehouse
VARIATION_SALT = "phase0-checkout-redesign/v1"

# A clear winner at n=5k: ~10% relative lift on a 30% base converts to a ~99%
# chance-to-beat-control without straying outside believable effect sizes (PLAN.md:98-101).
CONTROL_CONV_RATE = 0.30
TREATMENT_CONV_RATE = 0.33

# Order value is lognormal so revenue intervals look organically noisy (PLAN.md:99-101).
# AOV ~ $56; distribution identical across arms (the redesign lifts conversion, not AOV).
AOV_LOG_MEAN = 3.9
AOV_LOG_STD = 0.5

# Backdated running phase: started 21 days ago, still running (no dateEnded).
PHASE_DAYS = 21
# Conversions land within the default ~72h conversion window after each user's exposure
# (PLAN.md:93-94), not uniformly across the phase.
CONVERSION_WINDOW_HOURS = 72

# Warehouse table + fact-resource identifiers (Segment schema flavor).
EXPOSURE_TABLE = "experiment_viewed"
IDENTIFIES_TABLE = "identifies"
ORDERS_TABLE = "orders"

FACT_TABLE_ID = "ft_phase0_orders"
METRIC_CONVERSION_ID = "fact__phase0_purchase"
METRIC_REVENUE_ID = "fact__phase0_revenue_per_user"
