"""Declarative vertical-spec models (pydantic).

A vertical is fully described by one of these specs loaded from YAML: its scale, the
personas that make up its population, the metrics it tracks, and its roster of scripted
"story" experiments. The generation engine (core/*) is vertical-agnostic — adding a
vertical means writing a spec, not code.

This is the Phase 1 slice: enough surface to drive the SaaS onboarding story end-to-end.
Procedural-backfill experiments and richer funnels arrive when the verticals expand.
"""

from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, Field

MetricType = Literal["proportion", "mean"]


class Scale(BaseModel):
    """How big and how long. Events scale off the population and history window."""

    n_users: int = 25_000
    months: int = 12


class Persona(BaseModel):
    """A behavioural archetype. `weight` is relative (normalised across personas).

    Every vertical's funnel reduces to the same shape — a binary conversion and a value —
    so personas are described generically: `conversion_base` is P(convert) before propensity
    and experiment effect, and `value_log_*` are the lognormal params for the value a
    converting user contributes (revenue, MRR, transaction volume, ...).
    """

    name: str
    weight: float
    conversion_base: float = Field(ge=0.0, le=1.0)
    value_log_mean: float
    value_log_std: float


class Metric(BaseModel):
    """A metric definition mapped to an event in the generated `tracks` table."""

    key: str
    name: str
    type: MetricType
    event: str  # event name to filter the tracks table on
    value_column: str | None = None  # required for mean metrics (the numeric column)


class Variation(BaseModel):
    key: str
    name: str


class Story(BaseModel):
    """A hand-scripted experiment with a target outcome the solver verifies.

    The target chance-to-win alone encodes the story type: ~0.97 = win, ~0.50 = flat
    (inconclusive/running), ~0.05 = surprise loss. Experiments expose disjoint signup
    cohorts (via non-overlapping windows) so no user lands in two experiments.
    """

    key: str  # tracking key (== experiment_id in the warehouse)
    name: str
    phase_days: int = 60
    ends_days_ago: int = 0  # 0 => still running; >0 => stopped that many days ago
    exposure: Literal["signup", "active"] = "signup"
    primary_metric: str  # metric key the effect is applied to
    target_chance_to_win: float = Field(ge=0.0, le=1.0)  # the solver hits this at the real N
    # If set, this lift is used directly; otherwise the solver finds the lift that achieves
    # target_chance_to_win at the experiment's actual sample size.
    target_lift: float | None = None
    variations: list[Variation]

    @property
    def status(self) -> Literal["running", "stopped"]:
        return "running" if self.ends_days_ago == 0 else "stopped"


class Backfill(BaseModel):
    """Procedural backfill: N small experiments with effects drawn from a realistic prior.

    Most land flat/small, the occasional one wins or loses — the texture of a real org's
    experiment history (PLAN.md:178). Generated deterministically from the spec seed into
    disjoint historical windows behind the hand-scripted stories.
    """

    count: int = 5
    phase_days: int = 30
    starts_days_ago: int = 160  # first backfill window ends here, marching backwards
    lift_std: float = 0.05  # std of the prior on relative lift (centred at 0)
    primary_metric: str = "activation"


class VerticalSpec(BaseModel):
    name: str
    seed: int = 42
    schema_flavor: Literal["segment"] = "segment"
    scale: Scale = Scale()
    personas: list[Persona]
    metrics: list[Metric]
    stories: list[Story]
    backfill: Backfill | None = None

    def metric(self, key: str) -> Metric:
        return next(m for m in self.metrics if m.key == key)

    @property
    def conversion_metric(self) -> Metric:
        """The binary funnel metric the experiment effect is applied to."""
        return next(m for m in self.metrics if m.type == "proportion")

    @property
    def value_metric(self) -> Metric | None:
        """The per-user value metric (revenue/MRR/volume), if the vertical has one."""
        return next((m for m in self.metrics if m.type == "mean"), None)

    @classmethod
    def from_yaml(cls, path: str) -> VerticalSpec:
        with open(path) as f:
            return cls.model_validate(yaml.safe_load(f))
