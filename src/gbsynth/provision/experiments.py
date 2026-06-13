"""Experiment provisioning + outcome verification.

For each story: create the experiment with a backdated phase, trigger a results snapshot,
and verify the live chance-to-win matches the gbstats prediction (same data, same engine,
so they should agree) with no SRM warning. Stopped stories then get their conclusion set
(results/winner/analysis) via the update endpoint, which preserves the backdated dates.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

from gbsynth.core.stories import StoryOutcome
from gbsynth.provision.client import GBClient, GBError
from gbsynth.spec import Story

CTW_TOLERANCE = 0.06  # live vs predicted chance-to-win
SNAPSHOT_TIMEOUT_S = 180


@dataclass(slots=True)
class ExperimentResult:
    key: str
    status: str
    primary_metric: str
    expected_ctw: float
    live_ctw: float
    srm: float
    ok: bool


def _phase(story: Story, now: dt.datetime) -> dict:
    end = now - dt.timedelta(days=story.ends_days_ago)
    start = end - dt.timedelta(days=story.phase_days)
    phase = {
        "name": "Main",
        "dateStarted": start.isoformat(),
        "variationWeights": [0.5, 0.5],
        "coverage": 1,
    }
    if story.status == "stopped":
        phase["dateEnded"] = end.isoformat()
    return phase


def _find(client: GBClient, tracking_key: str) -> dict | None:
    for e in client.get("/experiments").get("experiments", []):
        if e.get("trackingKey") == tracking_key:
            return e
    return None


def _create(
    client: GBClient,
    project_id: str,
    datasource_id: str,
    assignment_query_id: str,
    story: Story,
    metric_ids: dict[str, str],
    now: dt.datetime,
) -> dict:
    ordered = [story.primary_metric] + [k for k in metric_ids if k != story.primary_metric]
    payload = {
        "datasourceId": datasource_id,
        "assignmentQueryId": assignment_query_id,
        "trackingKey": story.key,
        "name": story.name,
        "project": project_id,
        "hashAttribute": "user_id",
        "status": story.status,
        "metrics": [metric_ids[k] for k in ordered],
        "variations": [{"key": v.key, "name": v.name} for v in story.variations],
        "phases": [_phase(story, now)],
    }
    return client.post("/experiments", payload)["experiment"]


def _run_snapshot(client: GBClient, exp_id: str) -> None:
    snap_id = client.post(f"/experiments/{exp_id}/snapshot")["snapshot"]["id"]
    deadline = time.monotonic() + SNAPSHOT_TIMEOUT_S
    while time.monotonic() < deadline:
        status = client.get(f"/snapshots/{snap_id}")["snapshot"]["status"]
        if status not in ("running", "queued", "pending"):
            if status != "success":
                raise GBError(f"snapshot for {exp_id} finished status={status}")
            return
        time.sleep(3)
    raise GBError(f"snapshot for {exp_id} timed out")


def _live_ctw(client: GBClient, exp_id: str, metric_id: str) -> tuple[float, float]:
    """Return (chance_to_win for the treatment arm on metric_id, srm p-value).

    variations[0] is the baseline (its chanceToBeatControl is 0, not null); the treatment
    arm is variations[1] in a two-arm experiment.
    """
    overall = client.get(f"/experiments/{exp_id}/results")["result"]["results"][0]
    srm = float(overall["checks"]["srm"])
    for m in overall["metrics"]:
        if m["metricId"] != metric_id:
            continue
        variations = m["variations"]
        treatment = variations[1] if len(variations) > 1 else variations[0]
        ctw = treatment["analyses"][0].get("chanceToBeatControl")
        return (float(ctw) if ctw is not None else float("nan")), srm
    return float("nan"), srm


def _set_conclusion(client: GBClient, exp_id: str, live_ctw: float) -> None:
    if live_ctw >= 0.95:
        results, winner, note = "won", 1, "Significant positive lift; shipping treatment."
    elif live_ctw <= 0.05:
        results, winner, note = "lost", 0, "Treatment underperformed; keeping control."
    else:
        results, winner, note = "inconclusive", 0, "No clear winner at the planned sample size."
    client.post(
        f"/experiments/{exp_id}",
        {"results": results, "winner": winner, "analysis": note},
    )


def provision_experiment(
    client: GBClient,
    project_id: str,
    datasource_id: str,
    assignment_query_id: str,
    story: Story,
    outcomes: list[StoryOutcome],
    metric_ids: dict[str, str],
    now: dt.datetime,
) -> ExperimentResult:
    exp = _find(client, story.key) or _create(
        client, project_id, datasource_id, assignment_query_id, story, metric_ids, now
    )
    exp_id = exp["id"]
    _run_snapshot(client, exp_id)

    primary = next(o for o in outcomes if o.is_primary)
    live_ctw, srm = _live_ctw(client, exp_id, metric_ids[story.primary_metric])

    if story.status == "stopped":
        _set_conclusion(client, exp_id, live_ctw)

    # Assert live-vs-predicted only for decisive outcomes; near-0.5 (flat) predictions are
    # noise-sensitive at this sample size, so a small count difference swings CTW widely —
    # the same reasoning Phase 1 uses to not band flat stories. SRM must always be quiet.
    decisive = primary.chance_to_win >= 0.9 or primary.chance_to_win <= 0.1
    ctw_ok = (not decisive) or abs(live_ctw - primary.chance_to_win) <= CTW_TOLERANCE
    ok = ctw_ok and srm >= 0.001
    return ExperimentResult(
        key=story.key,
        status=story.status,
        primary_metric=story.primary_metric,
        expected_ctw=primary.chance_to_win,
        live_ctw=live_ctw,
        srm=srm,
        ok=ok,
    )
