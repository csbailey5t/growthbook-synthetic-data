"""Assignment engine: deterministic variation hashing, exposures, effect application.

Generalised from the Phase 0 spike. Variation is a stable hash of (user_id, story key)
so it is independent across experiments and reproducible — true randomization, which is
what keeps GrowthBook's SRM check quiet (PLAN.md:88-92). Each (user, story) maps to exactly
one variation, so no user ever appears in two arms.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from gbsynth.core.population import User
from gbsynth.spec import Story


def assign_variation(user_id: str, story_key: str, n_variations: int) -> int:
    """Deterministic variation index in [0, n_variations) from a stable hash."""
    digest = hashlib.sha256(f"{user_id}:{story_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % n_variations


@dataclass(slots=True)
class Exposure:
    user: User
    variation: int
    exposed_at: dt.datetime


def build_exposures(users: list[User], story: Story, now: dt.datetime) -> list[Exposure]:
    """Who is in the experiment and when they were exposed.

    The phase window is [now - ends_days_ago - phase_days, now - ends_days_ago); a `signup`
    exposure puts a user in the experiment if their signup falls in that half-open window.
    Disjoint windows across stories => each user is in at most one experiment, so generating
    events per-story never double-counts a user.
    """
    phase_end = now - dt.timedelta(days=story.ends_days_ago)
    phase_start = phase_end - dt.timedelta(days=story.phase_days)
    n_var = len(story.variations)
    exposures: list[Exposure] = []
    for u in users:
        if story.exposure == "signup":
            if not (phase_start <= u.signup_at < phase_end):
                continue
            exposed_at = u.signup_at
        else:  # "active": exposed at the later of signup and phase start
            if u.signup_at >= phase_end:
                continue
            exposed_at = max(u.signup_at, phase_start)
        exposures.append(Exposure(u, assign_variation(u.user_id, story.key, n_var), exposed_at))
    return exposures


def effect_multiplier(story: Story, metric_key: str, variation: int, lift: float) -> float:
    """Multiplicative lift applied to a user's metric probability/value.

    Only the treatment arm and only the story's primary metric are affected; everything
    else is left at 1.0 (unaffected), so secondary metrics move only through correlation.
    The `lift` is resolved per story (explicit or solved) before generation.
    """
    if variation != 0 and metric_key == story.primary_metric:
        return 1.0 + lift
    return 1.0
