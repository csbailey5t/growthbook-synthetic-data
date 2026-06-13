"""Event simulation: turn exposures into metric events (the `tracks` table).

For the SaaS slice the funnel is signup -> activation -> subscription:

  * activation (proportion metric): P(activate) = persona base x user propensity x effect.
  * MRR (mean metric): activated users get a lognormal monthly value; the effect is on
    activation, not value, so MRR-per-user rises only because more users activate — the
    same organically-correlated pattern proven in Phase 0.

Conversions land within CONVERSION_WINDOW_HOURS of exposure (PLAN.md:93-94), and any event
that would fall in the future simply hasn't happened yet — so recently-exposed users look
realistically un-converted.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from gbsynth.core.experiments import Exposure, effect_multiplier
from gbsynth.spec import Story

CONVERSION_WINDOW_HOURS = 72


@dataclass(slots=True)
class Event:
    user_id: str
    anonymous_id: str
    event: str
    received_at: dt.datetime
    value: float | None


def build_events(
    story: Story,
    exposures: list[Exposure],
    lift: float,
    rng: np.random.Generator,
    now: dt.datetime,
) -> list[Event]:
    n = len(exposures)
    window = CONVERSION_WINDOW_HOURS * 3600
    activate_draw = rng.random(n)
    delay = rng.uniform(0, window, n)
    mrr_noise = rng.standard_normal(n)

    events: list[Event] = []
    for i, exp in enumerate(exposures):
        u = exp.user
        prob = (
            u.activation_base
            * u.propensity
            * effect_multiplier(story, "activation", exp.variation, lift)
        )
        if activate_draw[i] >= min(prob, 1.0):
            continue
        occurred_at = exp.exposed_at + dt.timedelta(seconds=float(delay[i]))
        if occurred_at > now:  # hasn't happened yet
            continue
        mrr = float(np.exp(u.mrr_log_mean + u.mrr_log_std * mrr_noise[i]))
        events.append(Event(u.user_id, u.anonymous_id, "activated", occurred_at, None))
        events.append(Event(u.user_id, u.anonymous_id, "subscribed", occurred_at, round(mrr, 2)))
    return events
