"""Metric provisioning: the `tracks` fact table + one fact metric per spec metric.

Uses POST /v1/bulk-import/facts (upsert; resources get managedBy=api so demo users can't
break their definitions). Proportion metrics filter the events table to their event with
an empty numerator column; mean metrics sum a numeric value column. The `value` column is
double precision in the warehouse (the Phase 0 NUMERIC-as-string finding), so no cast.
"""

from __future__ import annotations

from gbsynth.provision.client import GBClient
from gbsynth.spec import VerticalSpec

FACT_TABLE_ID = "ft_saas_tracks"


def _metric_id(key: str) -> str:
    return f"fact__saas_{key}"


def import_metrics(
    client: GBClient, project_id: str, datasource_id: str, spec: VerticalSpec
) -> dict[str, str]:
    """Upsert the fact table + metrics; return {metric_key: metric_id}."""
    fact_metrics = []
    metric_ids: dict[str, str] = {}
    for m in spec.metrics:
        mid = _metric_id(m.key)
        metric_ids[m.key] = mid
        numerator: dict = {
            "factTableId": FACT_TABLE_ID,
            "rowFilters": [{"operator": "=", "column": "event", "values": [m.event]}],
        }
        if m.type == "proportion":
            numerator["column"] = ""
        else:
            numerator["column"] = m.value_column
            numerator["aggregation"] = "sum"
        fact_metrics.append(
            {
                "id": mid,
                "data": {
                    "name": m.name,
                    "metricType": m.type,
                    "numerator": numerator,
                    "projects": [project_id],
                    "managedBy": "api",
                },
            }
        )

    payload = {
        "factTables": [
            {
                "id": FACT_TABLE_ID,
                "data": {
                    "name": "SaaS Tracks",
                    "datasource": datasource_id,
                    "projects": [project_id],
                    "userIdTypes": ["user_id"],
                    "sql": "SELECT user_id, received_at AS timestamp, event, value FROM tracks",
                    "managedBy": "api",
                },
            }
        ],
        "factMetrics": fact_metrics,
    }
    client.post("/bulk-import/facts", payload)
    return metric_ids
