"""Statistical regression tests: generate -> gbstats -> assert the scripted outcome.

These lock in the contract that makes the whole project work — that the engine produces
data which GrowthBook's own stats engine reads as the intended story, with a balanced
split (no SRM). DB-free: pure generation + gbstats.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from gbsynth.build import build_dataset
from gbsynth.spec import Metric, Persona, Scale, Story, Variation, VerticalSpec

NOW = dt.datetime(2026, 6, 13, tzinfo=dt.UTC)
VERTICAL_SPECS = sorted(
    (Path(__file__).resolve().parent.parent / "config" / "verticals").glob("*.yaml")
)


@pytest.mark.parametrize("spec_path", VERTICAL_SPECS, ids=lambda p: p.stem)
def test_vertical_decisive_stories_verify(spec_path: Path) -> None:
    """Every shipped vertical's decisive (win/loss) stories land in band via gbstats."""
    spec = VerticalSpec.from_yaml(str(spec_path))
    dataset = build_dataset(spec, now=NOW)
    primaries = [o for o in dataset.outcomes if o.is_primary]
    bad = [(o.metric_key, round(o.chance_to_win, 3)) for o in primaries if not o.in_band]
    assert not bad, f"{spec_path.stem}: out-of-band {bad}"


def _slice_spec(n_users: int = 20_000) -> VerticalSpec:
    return VerticalSpec(
        name="saas-test",
        seed=7,
        scale=Scale(n_users=n_users, months=12),
        personas=[
            Persona(
                name="smb", weight=0.6, conversion_base=0.34, value_log_mean=3.4, value_log_std=0.5
            ),
            Persona(
                name="ent", weight=0.4, conversion_base=0.50, value_log_mean=5.0, value_log_std=0.6
            ),
        ],
        metrics=[
            Metric(key="activation", name="Activation", type="proportion", event="activated"),
            Metric(key="mrr", name="MRR", type="mean", event="subscribed", value_column="value"),
        ],
        stories=[
            Story(
                key="saas-onboarding-checklist",
                name="Onboarding checklist",
                phase_days=60,
                primary_metric="activation",
                target_chance_to_win=0.97,
                variations=[
                    Variation(key="0", name="Control"),
                    Variation(key="1", name="Treatment"),
                ],
            )
        ],
    )


def _primary(dataset):
    return next(o for o in dataset.outcomes if o.is_primary)


def test_onboarding_story_is_a_verified_win() -> None:
    dataset = build_dataset(_slice_spec(), now=NOW)
    primary = _primary(dataset)
    assert primary.metric_key == "activation"
    assert primary.lift > 0, "treatment should beat control"
    assert primary.chance_to_win >= 0.95, f"weak win: CTW={primary.chance_to_win}"
    assert primary.in_band


def test_secondary_mean_metric_also_rises() -> None:
    # MRR-per-user has no direct effect, but rises because more users activate.
    dataset = build_dataset(_slice_spec(), now=NOW)
    mrr = next(o for o in dataset.outcomes if o.metric_key == "mrr")
    assert mrr.metric_type == "mean"
    assert mrr.treatment_mean > mrr.control_mean


def test_generation_is_deterministic() -> None:
    a = _primary(build_dataset(_slice_spec(), now=NOW))
    b = _primary(build_dataset(_slice_spec(), now=NOW))
    assert (a.control_mean, a.treatment_mean, a.chance_to_win) == (
        b.control_mean,
        b.treatment_mean,
        b.chance_to_win,
    )


def test_assignment_split_has_no_srm() -> None:
    # Balanced first-exposure counts -> SRM chi-square stays quiet.
    dataset = build_dataset(_slice_spec(), now=NOW)
    exposures = next(t for t in dataset.tables if t.name == "experiment_viewed")
    variations = [row[4] for row in exposures.rows]  # variation_id column
    n = len(variations)
    treatment = sum(variations)
    assert 0.47 < treatment / n < 0.53, f"unbalanced split: {treatment}/{n}"


def _saas_dataset():
    from pathlib import Path

    spec_path = Path(__file__).resolve().parent.parent / "config" / "verticals" / "saas.yaml"
    return build_dataset(VerticalSpec.from_yaml(str(spec_path)), now=NOW)


def test_saas_roster_all_primaries_in_band() -> None:
    dataset = _saas_dataset()
    primaries = [o for o in dataset.outcomes if o.is_primary]
    assert all(o.in_band for o in primaries), [
        (o.metric_key, o.chance_to_win) for o in primaries if not o.in_band
    ]


def test_saas_roster_has_a_win_and_a_loss() -> None:
    dataset = _saas_dataset()
    primaries = [o for o in dataset.outcomes if o.is_primary]
    assert any(o.chance_to_win >= 0.9 for o in primaries), "expected a clear win"
    assert any(o.chance_to_win <= 0.2 for o in primaries), "expected a surprise loss"


def test_saas_roster_includes_backfill() -> None:
    # 3 hand-scripted stories + 5 backfill experiments.
    dataset = _saas_dataset()
    assert len(dataset.extra["story_results"]) == 8
    assert sum(1 for r in dataset.extra["story_results"] if "backfill" in r["key"]) == 5


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
