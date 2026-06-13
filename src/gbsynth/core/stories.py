"""Scripted-story solver + verification against gbstats.

Per PLAN.md:174-179 the approach is analytic-then-verify, not an iterative solver:

  1. Design check (analytic): plug the spec's (base rate, target lift, planned N) into
     gbstats and confirm the story *can* hit its target chance-to-win at that sample size.
  2. Verification (empirical): after generation, aggregate the real per-arm data, run it
     through gbstats — the exact engine GrowthBook runs — and assert the primary metric's
     chance-to-win lands in the target band. A story that passes here will display the
     intended result in the live UI (Phase 2) with no surprises.

The target chance-to-win encodes the story type (win ~0.97 / flat ~0.5 / loss ~0.05) and
drives the solver; only decisive win/loss primaries are asserted (flat is inconclusive).
"""

from __future__ import annotations

from dataclasses import dataclass

from gbstats.bayesian.tests import EffectBayesianABTest, EffectBayesianConfig
from gbstats.frequentist.tests import FrequentistConfig, TwoSidedTTest
from gbstats.models.statistics import ProportionStatistic, SampleMeanStatistic

from gbsynth.core.experiments import Exposure
from gbsynth.core.sessions import Event
from gbsynth.spec import Story, VerticalSpec

# The target chance-to-win drives the solver (what we aim for); verification asserts only
# that a single realization landed decisively on the correct side — exact-value bands would
# be flaky against sampling noise. Flat/running stories are inconclusive by design and not
# asserted.
WIN_FLOOR = 0.80  # a win must read as clearly winning
LOSS_CEILING = 0.20  # a loss must read as clearly losing


def _in_band(target_ctw: float, observed_ctw: float) -> bool:
    if target_ctw >= 0.9:  # win story
        return observed_ctw >= WIN_FLOOR
    if target_ctw <= 0.1:  # surprise-loss story
        return observed_ctw <= LOSS_CEILING
    return True  # flat / inconclusive


_REL = EffectBayesianConfig(difference_type="relative")
_REL_F = FrequentistConfig(difference_type="relative")


@dataclass(slots=True)
class StoryOutcome:
    metric_key: str
    metric_name: str
    metric_type: str
    control_mean: float
    treatment_mean: float
    lift: float
    chance_to_win: float
    p_value: float
    is_primary: bool
    in_band: bool


def _per_user(
    spec: VerticalSpec, exposures: list[Exposure], events: list[Event]
) -> dict[str, dict]:
    """Aggregate to one row per exposed user: variation, conversion flag, value sum."""
    conversion_event = spec.conversion_metric.event
    value_metric = spec.value_metric
    value_event = value_metric.event if value_metric else None

    rows: dict[str, dict] = {
        e.user.user_id: {"variation": e.variation, "converted": 0, "value": 0.0} for e in exposures
    }
    for ev in events:
        row = rows.get(ev.user_id)
        if row is None:
            continue
        if ev.event == conversion_event:
            row["converted"] = 1
        elif ev.event == value_event and ev.value is not None:
            row["value"] += ev.value
    return rows


def _stat(metric_type: str, values: list[float]):
    n = len(values)
    if metric_type == "proportion":
        return ProportionStatistic(n=n, sum=sum(values))
    return SampleMeanStatistic(n=n, sum=sum(values), sum_squares=sum(v * v for v in values))


def verify_story(
    spec: VerticalSpec, story: Story, exposures: list[Exposure], events: list[Event]
) -> list[StoryOutcome]:
    rows = _per_user(spec, exposures, events)
    control = [r for r in rows.values() if r["variation"] == 0]
    treatment = [r for r in rows.values() if r["variation"] != 0]

    outcomes: list[StoryOutcome] = []
    for metric in spec.metrics:
        col = "converted" if metric.type == "proportion" else "value"
        c_vals = [float(r[col]) for r in control]
        t_vals = [float(r[col]) for r in treatment]
        c_stat, t_stat = _stat(metric.type, c_vals), _stat(metric.type, t_vals)

        bayes = EffectBayesianABTest(c_stat, t_stat, _REL).compute_result()
        freq = TwoSidedTTest(c_stat, t_stat, _REL_F).compute_result()

        is_primary = metric.key == story.primary_metric
        ctw = float(bayes.chance_to_win)
        in_band = (not is_primary) or _in_band(story.target_chance_to_win, ctw)
        outcomes.append(
            StoryOutcome(
                metric_key=metric.key,
                metric_name=metric.name,
                metric_type=metric.type,
                control_mean=(sum(c_vals) / len(c_vals)) if c_vals else 0.0,
                treatment_mean=(sum(t_vals) / len(t_vals)) if t_vals else 0.0,
                lift=float(bayes.expected),
                chance_to_win=ctw,
                p_value=float(freq.p_value) if freq.p_value is not None else float("nan"),
                is_primary=is_primary,
                in_band=in_band,
            )
        )
    return outcomes


def blended_base(spec: VerticalSpec) -> float:
    """Population-weighted base conversion rate (the control-arm expectation)."""
    pw = sum(p.weight for p in spec.personas)
    return sum(p.weight / pw * p.conversion_base for p in spec.personas)


def _expected_ctw(base: float, lift: float, n_per_arm: int) -> float:
    c = ProportionStatistic(n=n_per_arm, sum=round(base * n_per_arm))
    t = ProportionStatistic(n=n_per_arm, sum=round(base * (1 + lift) * n_per_arm))
    return float(EffectBayesianABTest(c, t, _REL).compute_result().chance_to_win)


def solve_lift(base: float, n_per_arm: int, target_ctw: float, hi: float = 0.6) -> float:
    """Binary-search the relative lift that yields `target_ctw` at this sample size.

    chance-to-win is monotonic in lift at fixed N, so bisection converges. Searches negative
    lifts too, so the same solver scripts wins (target high), losses (target low), and flat
    (target ~0.5 -> lift ~0). This is the "compute the per-variation deltas that hit the
    target" step from PLAN.md:174-179.
    """
    lo = -hi
    for _ in range(40):
        mid = (lo + hi) / 2
        if _expected_ctw(base, mid, n_per_arm) < target_ctw:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def resolve_lift(spec: VerticalSpec, story: Story, n_per_arm: int) -> float:
    """The effect to apply: explicit target_lift, or solved from target_chance_to_win."""
    if story.target_lift is not None:
        return story.target_lift
    return solve_lift(blended_base(spec), max(1, n_per_arm), story.target_chance_to_win)


def design_check(spec: VerticalSpec, story: Story, lift: float, n_per_arm: int) -> float:
    """Expected chance-to-win for the resolved lift at the real per-arm sample size."""
    return _expected_ctw(blended_base(spec), lift, max(1, n_per_arm))
