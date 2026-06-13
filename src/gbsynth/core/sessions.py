"""Event simulation: turn exposures into metric events (the `tracks` table).

Every vertical's funnel reduces to the same shape, so this is spec-driven:

  * conversion (the proportion metric): P(convert) = persona base x propensity x effect.
  * value (the mean metric, optional): converting users emit a lognormal value; the effect
    is on conversion, not value, so value-per-user rises only because more users convert —
    the organically-correlated pattern proven in Phase 0.

The event *names* come from the spec's metrics, so adding a vertical is a spec change.
Conversions land within CONVERSION_WINDOW_HOURS of exposure (PLAN.md:93-94); an event that
would fall in the future simply hasn't happened yet.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from gbsynth.core.experiments import Exposure, effect_multiplier
from gbsynth.spec import Story, VerticalSpec

CONVERSION_WINDOW_HOURS = 72


@dataclass(slots=True)
class Event:
    user_id: str
    anonymous_id: str
    event: str
    received_at: dt.datetime
    value: float | None


def build_events(
    spec: VerticalSpec,
    story: Story,
    exposures: list[Exposure],
    lift: float,
    rng: np.random.Generator,
    now: dt.datetime,
) -> list[Event]:
    conversion_event = spec.conversion_metric.event
    value_metric = spec.value_metric
    value_event = value_metric.event if value_metric else None

    n = len(exposures)
    window = CONVERSION_WINDOW_HOURS * 3600
    convert_draw = rng.random(n)
    delay = rng.uniform(0, window, n)
    value_noise = rng.standard_normal(n)

    events: list[Event] = []
    for i, exp in enumerate(exposures):
        u = exp.user
        prob = (
            u.conversion_base
            * u.propensity
            * effect_multiplier(story, spec.conversion_metric.key, exp.variation, lift)
        )
        if convert_draw[i] >= min(prob, 1.0):
            continue
        occurred_at = exp.exposed_at + dt.timedelta(seconds=float(delay[i]))
        if occurred_at > now:  # hasn't happened yet
            continue
        events.append(Event(u.user_id, u.anonymous_id, conversion_event, occurred_at, None))
        if value_event is not None:
            value = float(np.exp(u.value_log_mean + u.value_log_std * value_noise[i]))
            events.append(
                Event(u.user_id, u.anonymous_id, value_event, occurred_at, round(value, 2))
            )
    return events
