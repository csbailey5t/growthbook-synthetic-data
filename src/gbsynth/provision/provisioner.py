"""End-to-end provisioning: dataset -> warehouse -> live GrowthBook objects, verified.

One call builds the complete project for a vertical. Generation, load, and provisioning
share a single `now` so the data GrowthBook queries matches the dataset the verifier
reasons about. Idempotent: reuses the project, data source, metrics (upsert), and
experiments (by tracking key) on re-run.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from gbsynth.build import build_dataset
from gbsynth.load.postgres import load_dataset
from gbsynth.provision import config
from gbsynth.provision.bootstrap import bootstrap_datasource
from gbsynth.provision.client import GBClient
from gbsynth.provision.experiments import ExperimentResult, provision_experiment
from gbsynth.provision.metrics import import_metrics
from gbsynth.provision.workspace import ensure_project
from gbsynth.spec import VerticalSpec


@dataclass(slots=True)
class ProvisionReport:
    project_id: str
    datasource_id: str
    experiments: list[ExperimentResult]
    loaded: dict[str, int]

    @property
    def ok(self) -> bool:
        return all(e.ok for e in self.experiments)


def provision(spec: VerticalSpec, now: dt.datetime | None = None) -> ProvisionReport:
    now = now or dt.datetime.now(dt.UTC).replace(microsecond=0)

    dataset = build_dataset(spec, now)
    loaded = load_dataset(dataset, config.LOADER_DSN)

    datasource_id, assignment_query_id = bootstrap_datasource()

    client = GBClient(config.GB_API_HOST, config.GB_API_KEY)
    project_id = ensure_project(client, f"gbsynth: {spec.name}")
    metric_ids = import_metrics(client, project_id, datasource_id, spec)

    experiments = [
        provision_experiment(
            client,
            project_id,
            datasource_id,
            assignment_query_id,
            r["story"],
            r["outcomes"],
            metric_ids,
            now,
        )
        for r in dataset.extra["story_results"]
    ]
    return ProvisionReport(project_id, datasource_id, experiments, loaded)
