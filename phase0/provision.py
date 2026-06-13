"""Provision the Phase 0 experiment in GrowthBook and verify the result.

Assumes:
  * The stack is up (docker compose up -d) and the toy data is loaded
    (uv run python -m phase0.generate).
  * You created a Postgres data source in the GrowthBook UI using the *Segment* schema,
    pointed at host "postgres" db "warehouse" (see phase0/README.md), and put an admin
    secret key in .env as GB_API_KEY.

What it does, idempotently:
  1. Discovers the data source id + its auto-generated assignment query id.
  2. Creates (or reuses) the "Phase 0 Spike" project.
  3. Imports the orders fact table + two fact metrics (proportion + mean) via
     bulk-import/facts, marked managedBy=api so the UI can't break their definitions.
  4. Creates (or reuses) the experiment with a backdated, still-running phase.
  5. Triggers a results snapshot and polls until it finishes.
  6. Reads the results and asserts no SRM warning, printing the computed lift.

Run:  uv run python -m phase0.provision
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from typing import Any

import httpx

from phase0 import config


class GBClient:
    """Thin GrowthBook REST wrapper: Bearer auth, base /api/v1, 429-aware retries.

    The full 60 req/min throttle is a Phase 2 concern; the spike makes ~10 calls, so we
    just back off politely when the server pushes back.
    """

    def __init__(self, host: str, key: str) -> None:
        if not key:
            sys.exit(
                "GB_API_KEY is empty in .env — create an admin secret key in the "
                "GrowthBook UI (Settings → API Keys) and add it first."
            )
        self._client = httpx.Client(
            base_url=f"{host}/api/v1",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30.0,
        )

    def request(self, method: str, path: str, **kwargs: Any) -> dict:
        for attempt in range(5):
            resp = self._client.request(method, path, **kwargs)
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2**attempt))
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                sys.exit(f"{method} {path} -> {resp.status_code}: {resp.text}")
            return resp.json()
        sys.exit(f"{method} {path} -> rate-limited after retries")

    def get(self, path: str, **kw: Any) -> dict:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload: dict) -> dict:
        return self.request("POST", path, json=payload)


def discover_datasource(gb: GBClient) -> tuple[str, str]:
    """Find the Postgres data source and its auto-generated assignment query id."""
    sources = gb.get("/data-sources").get("dataSources", [])
    if not sources:
        sys.exit(
            "No data sources found. Create the Postgres+Segment data source in the "
            "UI first (see phase0/README.md), then re-run."
        )
    ds = next((s for s in sources if s.get("type") == "postgres"), sources[0])
    queries = ds.get("assignmentQueries", [])
    if not queries:
        sys.exit(
            f"Data source {ds['id']} has no assignment queries — the Segment schema "
            "should auto-create one. Check the data source config in the UI."
        )
    # Segment auto-creates two queries (anonymous_id + user_id). Prefer user_id so it
    # matches the metrics' identifier and no identity join is needed.
    query = next((q for q in queries if q.get("identifierType") == "user_id"), queries[0])
    print(f"  data source: {ds['id']} ({ds.get('type')}), assignment query: {query['id']}")
    return ds["id"], query["id"]


def ensure_project(gb: GBClient) -> str:
    existing = gb.get("/projects").get("projects", [])
    for p in existing:
        if p.get("name") == config.PROJECT_NAME:
            print(f"  project: reusing {p['id']}")
            return p["id"]
    pid = gb.post(
        "/projects",
        {"name": config.PROJECT_NAME, "description": "GrowthBook synthetic-data Phase 0 spike."},
    )
    project_id = pid["project"]["id"]
    print(f"  project: created {project_id}")
    return project_id


def import_facts(gb: GBClient, project_id: str, datasource_id: str) -> None:
    """Upsert the orders fact table + two fact metrics. managedBy=api locks them in the UI."""
    payload = {
        "factTables": [
            {
                "id": config.FACT_TABLE_ID,
                "data": {
                    "name": "Phase 0 Orders",
                    "datasource": datasource_id,
                    "projects": [project_id],
                    "userIdTypes": ["user_id"],
                    # Cast amount to double precision: the Postgres driver returns NUMERIC
                    # as a string, which GrowthBook's column detection types as "string" —
                    # blocking sum aggregation on the mean metric. The warehouse column
                    # stays NUMERIC (correct for money); we cast only at the fact-table SQL.
                    "sql": f"SELECT user_id, received_at AS timestamp, "
                    f"CAST(amount AS double precision) AS amount FROM {config.ORDERS_TABLE}",
                    "managedBy": "api",
                },
            }
        ],
        "factMetrics": [
            {
                # Proportion: share of exposed users with >=1 order. column must be empty.
                "id": config.METRIC_CONVERSION_ID,
                "data": {
                    "name": "Purchase conversion",
                    "metricType": "proportion",
                    "numerator": {"factTableId": config.FACT_TABLE_ID, "column": ""},
                    "projects": [project_id],
                    "managedBy": "api",
                },
            },
            {
                # Mean: revenue per exposed user (sum of amount per user, 0 if no order).
                "id": config.METRIC_REVENUE_ID,
                "data": {
                    "name": "Revenue per user",
                    "metricType": "mean",
                    "numerator": {
                        "factTableId": config.FACT_TABLE_ID,
                        "column": "amount",
                        "aggregation": "sum",
                    },
                    "projects": [project_id],
                    "managedBy": "api",
                },
            },
        ],
    }
    res = gb.post("/bulk-import/facts", payload)
    print(
        f"  facts: tables +{res['factTablesAdded']}/~{res['factTablesUpdated']}, "
        f"metrics +{res['factMetricsAdded']}/~{res['factMetricsUpdated']}"
    )


def ensure_experiment(
    gb: GBClient, project_id: str, datasource_id: str, assignment_query_id: str
) -> str:
    for e in gb.get("/experiments").get("experiments", []):
        if e.get("trackingKey") == config.EXPERIMENT_KEY:
            print(f"  experiment: reusing {e['id']}")
            return e["id"]

    phase_start = dt.datetime.now(dt.UTC) - dt.timedelta(days=config.PHASE_DAYS)
    payload = {
        "datasourceId": datasource_id,
        "assignmentQueryId": assignment_query_id,
        "trackingKey": config.EXPERIMENT_KEY,
        "name": config.EXPERIMENT_NAME,
        "project": project_id,
        "hypothesis": "The checkout redesign lifts purchase conversion.",
        "hashAttribute": "user_id",
        "status": "running",
        "metrics": [config.METRIC_CONVERSION_ID, config.METRIC_REVENUE_ID],
        "variations": [
            {"key": "0", "name": "Control"},
            {"key": "1", "name": "Treatment"},
        ],
        "phases": [
            {
                "name": "Main",
                "dateStarted": phase_start.isoformat(),
                "variationWeights": [0.5, 0.5],
                "coverage": 1,
            }
        ],
    }
    exp = gb.post("/experiments", payload)
    exp_id = exp["experiment"]["id"]
    print(f"  experiment: created {exp_id}")
    return exp_id


def run_snapshot(gb: GBClient, exp_id: str) -> None:
    snap = gb.post(f"/experiments/{exp_id}/snapshot", {})["snapshot"]
    snap_id = snap["id"]
    print(f"  snapshot: {snap_id} triggered, polling...", end="", flush=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = gb.get(f"/snapshots/{snap_id}")["snapshot"]["status"]
        if status not in ("running", "queued", "pending"):
            print(f" {status}")
            if status != "success":
                sys.exit(f"Snapshot finished with status={status} (expected success).")
            return
        print(".", end="", flush=True)
        time.sleep(3)
    sys.exit(" timed out waiting for snapshot")


def verify_results(gb: GBClient, exp_id: str) -> None:
    res = gb.get(f"/experiments/{exp_id}/results")["result"]
    overall = res["results"][0]  # dimension "" == overall
    srm = overall["checks"]["srm"]
    print(f"\nResults ({overall['totalUsers']} users):")
    print(
        f"  SRM p-value: {srm:.4f}  ({'OK — no warning' if srm >= 0.001 else 'WARNING (<0.001)'})"
    )

    for m in overall["metrics"]:
        print(f"  metric '{m['metricName']}':")
        for v in m["variations"]:
            analysis = v["analyses"][0]
            c7 = analysis.get("chanceToBeatControl")
            ctw = f"{c7:.1%}" if c7 is not None else "n/a (baseline)"
            print(
                f"    {v['variationName']:<10} mean={analysis['mean']:.4f}  "
                f"lift={analysis['percentChange']:+.2%}  chanceToWin={ctw}"
            )

    if srm < 0.001:
        sys.exit("\nFAIL: SRM warning present — the assignment data is not balanced.")
    print("\nPASS: result computed by GrowthBook's engine, no SRM warning.")


def main() -> None:
    gb = GBClient(config.GB_API_HOST, config.GB_API_KEY)
    print("Provisioning Phase 0 spike in GrowthBook...")
    datasource_id, assignment_query_id = discover_datasource(gb)
    project_id = ensure_project(gb)
    import_facts(gb, project_id, datasource_id)
    exp_id = ensure_experiment(gb, project_id, datasource_id, assignment_query_id)
    run_snapshot(gb, exp_id)
    verify_results(gb, exp_id)


if __name__ == "__main__":
    main()
