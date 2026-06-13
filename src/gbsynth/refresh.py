"""Rolling freshness: keep a provisioned workspace current (PLAN.md:181-186, 296-302).

Because every experiment window is computed relative to `now` (running stories are always
mid-phase, stopped ones recently concluded, signups span the trailing 12 months), simply
regenerating with today's date and reloading the warehouse makes the whole workspace
current. Refresh then re-triggers each experiment's snapshot so the UI results reflect the
fresh data. The load is idempotent (delete-by-partition), so re-running is safe.

Assumes the vertical is already provisioned (run `gbsynth provision <vertical>` first);
refresh advances data + results, it does not create GrowthBook objects. True story
rotation (retiring a finished live experiment and promoting the next) is a future
enhancement — the now-relative windows already keep a live experiment perpetually mid-flight.
"""

from __future__ import annotations

import datetime as dt

from gbsynth.build import build_dataset
from gbsynth.load.postgres import load_dataset
from gbsynth.provision import config
from gbsynth.provision.client import GBClient
from gbsynth.provision.experiments import _find, _run_snapshot
from gbsynth.spec import VerticalSpec


def refresh(spec: VerticalSpec, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.UTC).replace(microsecond=0)

    dataset = build_dataset(spec, now)
    loaded = load_dataset(dataset, config.loader_dsn(spec.name))

    client = GBClient(config.GB_API_HOST, config.GB_API_KEY)
    snapshotted: list[str] = []
    missing: list[str] = []
    for r in dataset.extra["story_results"]:
        exp = _find(client, r["key"])
        if exp is None:
            missing.append(r["key"])
            continue
        _run_snapshot(client, exp["id"])
        snapshotted.append(r["key"])

    return {"loaded": loaded, "snapshotted": snapshotted, "missing": missing}
