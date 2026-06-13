"""Workspace audit: is the provisioned demo still healthy?

A pre-demo check (PLAN.md Phase 4) that re-snapshots each hand-scripted experiment and
confirms it still reads correctly: no SRM warning, and decisive stories land on the right
side (wins winning, losses losing). It needs only the spec + a live GrowthBook — no
warehouse rebuild — so it's safe to run right before a demo. Read-only except for
triggering snapshot refreshes.
"""

from __future__ import annotations

from dataclasses import dataclass

from gbsynth.core.stories import _in_band
from gbsynth.provision import config
from gbsynth.provision.client import GBClient
from gbsynth.provision.experiments import _find, _live_ctw, _run_snapshot
from gbsynth.provision.metrics import metric_id
from gbsynth.spec import VerticalSpec


@dataclass(slots=True)
class VerifyResult:
    key: str
    found: bool
    srm: float
    live_ctw: float
    healthy: bool
    note: str


def verify(spec: VerticalSpec) -> list[VerifyResult]:
    client = GBClient(config.GB_API_HOST, config.GB_API_KEY)
    primary_metric_id = metric_id(spec.name, spec.conversion_metric.key)

    results: list[VerifyResult] = []
    for story in spec.stories:
        exp = _find(client, story.key)
        if exp is None:
            results.append(
                VerifyResult(story.key, False, float("nan"), float("nan"), False, "not provisioned")
            )
            continue
        _run_snapshot(client, exp["id"])
        live_ctw, srm = _live_ctw(client, exp["id"], primary_metric_id)
        srm_ok = srm >= 0.001
        dir_ok = _in_band(story.target_chance_to_win, live_ctw)
        note = "ok"
        if not srm_ok:
            note = "SRM warning"
        elif not dir_ok:
            note = "outcome drifted off its scripted side"
        results.append(VerifyResult(story.key, True, srm, live_ctw, srm_ok and dir_ok, note))
    return results
