"""End-to-end provisioning: dataset -> warehouse -> live GrowthBook objects, verified.

One call builds the complete project for a vertical. Generation, load, and provisioning
share a single `now` so the data GrowthBook queries matches the dataset the verifier
reasons about. Idempotent: reuses the project, data source, metrics (upsert), and
experiments (by tracking key) on re-run.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import psycopg

from gbsynth.build import build_dataset
from gbsynth.load.postgres import load_dataset
from gbsynth.provision import config
from gbsynth.provision.bootstrap import bootstrap_datasource
from gbsynth.provision.client import GBClient
from gbsynth.provision.experiments import ExperimentResult, provision_experiment
from gbsynth.provision.features import provision_features
from gbsynth.provision.metrics import import_metrics
from gbsynth.provision.workspace import ensure_project
from gbsynth.spec import VerticalSpec


@dataclass(slots=True)
class ProvisionReport:
    project_id: str
    datasource_id: str
    experiments: list[ExperimentResult]
    loaded: dict[str, int]
    features_created: int = 0

    @property
    def ok(self) -> bool:
        return all(e.ok for e in self.experiments)


def _ensure_database(db: str) -> None:
    """Create the vertical's warehouse database if it doesn't exist (CREATE has no IF NOT
    EXISTS, so check pg_database first)."""
    with psycopg.connect(config.admin_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        if cur.fetchone() is None:
            # db is the vertical name from our own spec (trusted, not user input).
            cur.execute(f'CREATE DATABASE "{db}"')  # ty: ignore[no-matching-overload]


def provision(
    spec: VerticalSpec, now: dt.datetime | None = None, warehouse: str = "postgres"
) -> ProvisionReport:
    now = now or dt.datetime.now(dt.UTC).replace(microsecond=0)
    warehouse_db = spec.name  # one database per vertical

    dataset = build_dataset(spec, now)
    if warehouse == "clickhouse":
        from gbsynth.load.clickhouse import load_dataset as ch_load

        loaded = ch_load(dataset, database=warehouse_db)
    else:
        _ensure_database(warehouse_db)
        loaded = load_dataset(dataset, config.loader_dsn(warehouse_db))

    datasource_id, assignment_query_id = bootstrap_datasource(spec.name, warehouse_db, warehouse)

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
    features_created = provision_features(client, project_id, spec)
    return ProvisionReport(project_id, datasource_id, experiments, loaded, features_created)
