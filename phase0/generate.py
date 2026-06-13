"""Generate the Phase 0 toy dataset and load it into Postgres.

One experiment, 5k users, two metrics' worth of events — the smallest dataset that can
prove GrowthBook's stats engine computes a believable, warning-free result from synthetic
warehouse data.

Realism guarantees baked in here (PLAN.md:87-101):
  * Variation assigned by sha256(user_id, salt) % 2 — true independent randomization, so
    first-exposure counts pass GrowthBook's SRM chi-squared check.
  * Exactly one exposure row per user with a deterministic variation — no user ever
    appears in two variations, so the multiple-exposure check stays quiet.
  * Conversions land within CONVERSION_WINDOW_HOURS after each user's own exposure, not
    uniformly across the phase.
  * Order value is lognormal so revenue confidence intervals look organically noisy.

Schema flavor: Segment. Column names (user_id, anonymous_id, received_at, experiment_id,
variation_id) match what GrowthBook's Segment auto-configuration expects, so the data
source created in the UI "just works" with no hand-written assignment SQL.

Run:  uv run python -m phase0.generate
"""

from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np
import psycopg
from psycopg import sql

from phase0 import config


def assign_variation(user_id: str) -> int:
    """Deterministic 50/50 variation from a stable hash. 0 = control, 1 = treatment."""
    digest = hashlib.sha256(f"{user_id}:{config.VARIATION_SALT}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 2


# Segment-standard `context_*` columns. GrowthBook's Segment auto-config derives the
# source/medium/device/browser experiment dimensions from exactly these (discovered by
# capturing the UI-created data source — see phase0/README.md), so the generated table
# must carry them or the auto-generated assignment SQL fails on missing columns.
SOURCE_MEDIUM = [
    ("google", "cpc"),
    ("google", "organic"),
    ("direct", "none"),
    ("email", "email"),
    ("facebook", "social"),
]
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


def build_rows(now: dt.datetime) -> tuple[list, list, list]:
    """Return (identifies, exposures, orders) row tuples for the toy dataset."""
    rng = np.random.default_rng(config.SEED)
    phase_start = now - dt.timedelta(days=config.PHASE_DAYS)
    phase_span = (now - phase_start).total_seconds()
    window = config.CONVERSION_WINDOW_HOURS * 3600

    # Pre-draw vectorized randomness keyed off the seed.
    expose_offsets = rng.uniform(0, phase_span, config.N_USERS)
    conv_draws = rng.random(config.N_USERS)
    conv_delays = rng.uniform(0, window, config.N_USERS)
    amounts = rng.lognormal(config.AOV_LOG_MEAN, config.AOV_LOG_STD, config.N_USERS)
    # Dimensions drawn independently of variation, so splits stay balanced (PLAN.md:104).
    src_idx = rng.integers(0, len(SOURCE_MEDIUM), config.N_USERS)
    ua_idx = rng.integers(0, len(USER_AGENTS), config.N_USERS)

    identifies, exposures, orders = [], [], []
    for i in range(config.N_USERS):
        user_id = f"u_{i:05d}"
        anon_id = f"anon_{i:05d}"
        variation = assign_variation(user_id)
        exposed_at = phase_start + dt.timedelta(seconds=float(expose_offsets[i]))
        source, medium = SOURCE_MEDIUM[int(src_idx[i])]
        user_agent = USER_AGENTS[int(ua_idx[i])]

        identifies.append((user_id, anon_id, exposed_at))
        exposures.append(
            (
                user_id,
                anon_id,
                exposed_at,
                config.EXPERIMENT_KEY,
                variation,
                source,
                medium,
                user_agent,
            )
        )

        rate = config.TREATMENT_CONV_RATE if variation == 1 else config.CONTROL_CONV_RATE
        if conv_draws[i] < rate:
            ordered_at = exposed_at + dt.timedelta(seconds=float(conv_delays[i]))
            # A conversion that would fall in the future hasn't happened yet — drop it.
            if ordered_at <= now:
                orders.append((user_id, ordered_at, f"o_{i:05d}", round(float(amounts[i]), 2)))

    return identifies, exposures, orders


DDL = f"""
DROP TABLE IF EXISTS {config.IDENTIFIES_TABLE};
DROP TABLE IF EXISTS {config.EXPOSURE_TABLE};
DROP TABLE IF EXISTS {config.ORDERS_TABLE};

CREATE TABLE {config.IDENTIFIES_TABLE} (
    user_id      text        NOT NULL,
    anonymous_id text        NOT NULL,
    received_at  timestamptz NOT NULL
);

CREATE TABLE {config.EXPOSURE_TABLE} (
    user_id                 text        NOT NULL,
    anonymous_id            text        NOT NULL,
    received_at             timestamptz NOT NULL,
    experiment_id           text        NOT NULL,
    variation_id            smallint    NOT NULL,
    context_campaign_source text        NOT NULL,
    context_campaign_medium text        NOT NULL,
    context_user_agent      text        NOT NULL
);

CREATE TABLE {config.ORDERS_TABLE} (
    user_id     text        NOT NULL,
    received_at timestamptz NOT NULL,
    order_id    text        NOT NULL,
    amount      numeric     NOT NULL
);
"""

COPY_COLUMNS = {
    config.IDENTIFIES_TABLE: ("user_id", "anonymous_id", "received_at"),
    config.EXPOSURE_TABLE: (
        "user_id",
        "anonymous_id",
        "received_at",
        "experiment_id",
        "variation_id",
        "context_campaign_source",
        "context_campaign_medium",
        "context_user_agent",
    ),
    config.ORDERS_TABLE: ("user_id", "received_at", "order_id", "amount"),
}


def _copy_stmt(table: str, columns: tuple[str, ...]) -> sql.Composed:
    return sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
    )


def load(identifies: list, exposures: list, orders: list) -> None:
    with psycopg.connect(config.PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        for table, rows in (
            (config.IDENTIFIES_TABLE, identifies),
            (config.EXPOSURE_TABLE, exposures),
            (config.ORDERS_TABLE, orders),
        ):
            with cur.copy(_copy_stmt(table, COPY_COLUMNS[table])) as copy:
                for row in rows:
                    copy.write_row(row)
        conn.commit()


def main() -> None:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    identifies, exposures, orders = build_rows(now)

    control = sum(1 for e in exposures if e[4] == 0)  # e[4] == variation_id
    treatment = len(exposures) - control
    print(f"Generated {len(exposures)} exposures  (control={control}, treatment={treatment})")
    print(
        f"Generated {len(orders)} orders         (overall conv ~{len(orders) / len(exposures):.1%})"
    )

    load(identifies, exposures, orders)
    print(
        f"Loaded into Postgres ({config.PG_DB}): "
        f"{config.IDENTIFIES_TABLE}, {config.EXPOSURE_TABLE}, {config.ORDERS_TABLE}"
    )


if __name__ == "__main__":
    main()
