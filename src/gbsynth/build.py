"""Top-level generation pipeline: spec -> Dataset (tables + verified story outcomes).

Vertical-agnostic. Deterministic for a given (spec.seed): re-running reproduces the exact
dataset, which is what makes idempotent loads and reproducible verification possible.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from gbsynth.core.experiments import build_exposures
from gbsynth.core.population import build_users
from gbsynth.core.sessions import build_events
from gbsynth.core.stories import StoryOutcome, design_check, resolve_lift, verify_story
from gbsynth.dataset import Dataset, Table
from gbsynth.schemas import common, segment
from gbsynth.spec import Story, Variation, VerticalSpec


def _backfill_stories(spec: VerticalSpec, rng: np.random.Generator) -> list[Story]:
    """Procedural backfill: small experiments with effects drawn from a prior (PLAN.md:178).

    Effects are explicit (drawn here), so they aren't solved or asserted — most land flat,
    the occasional one is significant, giving the org a realistic experiment history. Each
    occupies a disjoint historical window behind the hand-scripted stories.
    """
    bf = spec.backfill
    if bf is None:
        return []
    lifts = rng.normal(0.0, bf.lift_std, bf.count)
    variations = [Variation(key="0", name="Control"), Variation(key="1", name="Treatment")]
    stories = []
    for i in range(bf.count):
        stories.append(
            Story(
                key=f"{spec.name}-backfill-{i + 1:02d}",
                name=f"Backfill experiment {i + 1}",
                phase_days=bf.phase_days,
                ends_days_ago=bf.starts_days_ago + i * bf.phase_days,
                primary_metric=bf.primary_metric,
                target_chance_to_win=0.5,  # not asserted; the explicit lift defines it
                target_lift=round(float(lifts[i]), 4),
                variations=variations,
            )
        )
    return stories


def build_dataset(spec: VerticalSpec, now: dt.datetime | None = None) -> Dataset:
    now = now or dt.datetime.now(dt.UTC).replace(microsecond=0)
    rng = np.random.default_rng(spec.seed)

    users = build_users(spec, now, rng)
    stories = list(spec.stories) + _backfill_stories(spec, rng)

    exposure_table: Table | None = None
    exposure_rows: list[tuple] = []
    all_events = []
    all_outcomes: list[StoryOutcome] = []
    story_results = []

    for story in stories:
        exposures = build_exposures(users, story, now)
        # Assignment is independent of the effect, so we can size each arm first and solve
        # the lift that hits the target chance-to-win at the real sample size.
        n_per_arm = min(
            sum(1 for e in exposures if e.variation == 0),
            sum(1 for e in exposures if e.variation != 0),
        )
        lift = resolve_lift(spec, story, n_per_arm)
        events = build_events(story, exposures, lift, rng, now)
        outcomes = verify_story(spec, story, exposures, events)
        expected_ctw = design_check(spec, story, lift, n_per_arm)

        ev_table = segment.experiment_viewed(exposures, story.key)
        exposure_table = exposure_table or ev_table
        exposure_rows.extend(ev_table.rows)
        all_events.extend(events)
        all_outcomes.extend(outcomes)
        story_results.append(
            {
                "key": story.key,
                "name": story.name,
                "status": story.status,
                "n_exposed": len(exposures),
                "planned_n": n_per_arm,
                "resolved_lift": lift,
                "expected_ctw": expected_ctw,
                "outcomes": outcomes,
            }
        )

    assert exposure_table is not None, "spec defines no stories"
    merged_exposures = Table(
        name=exposure_table.name,
        columns=exposure_table.columns,
        rows=exposure_rows,
        partition_column=exposure_table.partition_column,
    )

    tables = [
        common.users_table(users),
        segment.identifies(users),
        merged_exposures,
        common.tracks_table(all_events),
    ]

    window_start = now - dt.timedelta(days=spec.scale.months * 30)
    return Dataset(
        tables=tables,
        outcomes=all_outcomes,
        window_start=window_start,
        window_end=now,
        extra={"story_results": story_results},
    )
