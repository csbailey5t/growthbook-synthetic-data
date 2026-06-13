"""Population generation: personas, stable latent propensities, and a signup curve.

Each user gets a single persona and a stable latent *propensity* — a multiplier drawn
once and reused everywhere their behaviour is simulated. That stability is the hook CUPED
needs later (PLAN.md:95-97): a propensity that drives both pre- and post-exposure activity
is what makes regression adjustment visibly tighten intervals. For the slice it modulates
activation.

The signup curve combines gradual growth with weekly seasonality so "users over time"
charts look organic (PLAN.md:104).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from gbsynth.spec import VerticalSpec

# Segment-standard context dimensions, graduated from the Phase 0 spike. GrowthBook's
# Segment auto-config derives source/medium/device/browser from exactly these columns.
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
COUNTRIES = ["US", "GB", "CA", "DE", "AU", "FR", "IN", "BR"]


@dataclass(slots=True)
class User:
    idx: int
    user_id: str
    anonymous_id: str
    persona: str
    activation_base: float  # persona base, captured for convenience
    mrr_log_mean: float
    mrr_log_std: float
    propensity: float  # stable latent multiplier (~1)
    signup_at: dt.datetime
    country: str
    source: str
    medium: str
    user_agent: str


def _signup_times(rng: np.random.Generator, n: int, days: int, now: dt.datetime) -> np.ndarray:
    """Allocate `n` signups across `days` with linear growth + weekday seasonality."""
    start = now - dt.timedelta(days=days)
    day_dates = [start + dt.timedelta(days=d) for d in range(days)]
    growth = np.linspace(1.0, 2.0, days)  # newer days busier
    weekday = np.array([0.6 if d.weekday() >= 5 else 1.0 for d in day_dates])  # quiet weekends
    weights = growth * weekday
    counts = rng.multinomial(n, weights / weights.sum())

    times = np.empty(n, dtype="datetime64[s]")
    pos = 0
    for d in range(days):
        c = counts[d]
        if not c:
            continue
        secs = rng.uniform(0, 86_400, c)
        base = np.datetime64(int((day_dates[d]).timestamp()), "s")
        times[pos : pos + c] = base + secs.astype("timedelta64[s]")
        pos += c
    return times


def build_users(spec: VerticalSpec, now: dt.datetime, rng: np.random.Generator) -> list[User]:
    n = spec.scale.n_users
    personas = spec.personas
    pweights = np.array([p.weight for p in personas])
    pweights = pweights / pweights.sum()

    persona_idx = rng.choice(len(personas), size=n, p=pweights)
    # Propensity centred near 1 (median 1; lognormal so a long upper tail exists).
    propensity = rng.lognormal(0.0, 0.3, n)
    signup_at = _signup_times(rng, n, spec.scale.months * 30, now)
    country = rng.choice(len(COUNTRIES), size=n)
    src = rng.integers(0, len(SOURCE_MEDIUM), n)
    ua = rng.integers(0, len(USER_AGENTS), n)

    users: list[User] = []
    for i in range(n):
        p = personas[int(persona_idx[i])]
        source, medium = SOURCE_MEDIUM[int(src[i])]
        users.append(
            User(
                idx=i,
                user_id=f"u_{i:06d}",
                anonymous_id=f"anon_{i:06d}",
                persona=p.name,
                activation_base=p.activation_base,
                mrr_log_mean=p.mrr_log_mean,
                mrr_log_std=p.mrr_log_std,
                propensity=float(propensity[i]),
                signup_at=signup_at[i]
                .astype("datetime64[s]")
                .astype(dt.datetime)
                .replace(tzinfo=dt.UTC),
                country=COUNTRIES[int(country[i])],
                source=source,
                medium=medium,
                user_agent=USER_AGENTS[int(ua[i])],
            )
        )
    return users
