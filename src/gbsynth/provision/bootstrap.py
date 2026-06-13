"""Seed the Postgres data-source document directly into GrowthBook's Mongo.

Data sources are read-only via the REST API (the one gap, PLAN.md:52-56), so the only way
to make the org bootstrappable from zero is to write the document ourselves — with
credentials encrypted under the same ENCRYPTION_KEY the app uses (crypto.py). The Segment
`settings` below are the captured-and-verified shape from the Phase 0 spike; the exposure
queries read public.experiment_viewed (+ context_* columns) and identities join on
public.identifies — exactly what the gbsynth Segment schema produces.

Idempotent: reuses an existing data source with the same name.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

from bson import ObjectId
from pymongo import MongoClient

from gbsynth.provision import config, crypto


def _datasource_name(vertical: str) -> str:
    return f"gbsynth {vertical} Warehouse"


def _exposure_sql(id_col: str, exposure_table: str) -> str:
    return (
        f"SELECT\n  {id_col},\n  received_at as timestamp,\n  experiment_id,\n"
        "  variation_id,\n  context_campaign_source as source,\n"
        "  context_campaign_medium as medium,\n"
        "  (CASE\n    WHEN context_user_agent LIKE '%Mobile%' THEN 'Mobile'\n"
        "    ELSE 'Tablet/Desktop' END\n  ) as device,\n"
        "  (CASE\n    WHEN context_user_agent LIKE '% Firefox%' THEN 'Firefox'\n"
        "    WHEN context_user_agent LIKE '% OPR%' THEN 'Opera'\n"
        "    WHEN context_user_agent LIKE '% Edg%' THEN 'Edge'\n"
        "    WHEN context_user_agent LIKE '% Chrome%' THEN 'Chrome'\n"
        "    WHEN context_user_agent LIKE '% Safari%' THEN 'Safari'\n"
        f"    ELSE 'Other' END\n  ) as browser\nFROM\n  {exposure_table}\n"
        f"WHERE\n  {id_col} is not null"
    )


def _segment_settings(exposure_table: str, identifies_table: str) -> dict:
    """Segment-shape settings. Table refs are schema-qualified for Postgres (public.*) but
    bare for ClickHouse, which has no `public` schema."""
    return {
        "schemaFormat": "segment",
        "userIdTypes": [
            {"userIdType": "anonymous_id", "description": "Anonymous visitor id"},
            {"userIdType": "user_id", "description": "Logged-in user id"},
        ],
        "queries": {
            "exposure": [
                {
                    "id": "anonymous_id",
                    "userIdType": "anonymous_id",
                    "dimensions": ["source", "medium", "device", "browser"],
                    "name": "Anonymous Visitors",
                    "description": "",
                    "query": _exposure_sql("anonymous_id", exposure_table),
                },
                {
                    "id": "user_id",
                    "userIdType": "user_id",
                    "dimensions": ["source", "medium", "device", "browser"],
                    "name": "Logged-in Users",
                    "description": "",
                    "query": _exposure_sql("user_id", exposure_table),
                },
            ],
            "identityJoins": [
                {
                    "ids": ["user_id", "anonymous_id"],
                    "query": f"SELECT\n  user_id,\n  anonymous_id\nFROM\n  {identifies_table}",
                }
            ],
        },
        "schemaOptions": {"exposureTableName": "experiment_viewed"},
    }


def _settings_and_params(warehouse_type: str, warehouse_db: str) -> tuple[dict, str]:
    if warehouse_type == "clickhouse":
        settings = _segment_settings("experiment_viewed", "identifies")
        return settings, json.dumps(config.clickhouse_params(warehouse_db))
    settings = _segment_settings("public.experiment_viewed", "public.identifies")
    return settings, json.dumps(config.datasource_params(warehouse_db))


def _user_id_query(settings: dict) -> str:
    """The assignment query keyed on user_id (matches the metrics' identifier)."""
    for q in settings["queries"]["exposure"]:
        if q["userIdType"] == "user_id":
            return q["id"]
    return settings["queries"]["exposure"][0]["id"]


def bootstrap_datasource(
    vertical: str, warehouse_db: str, warehouse_type: str = "postgres"
) -> tuple[str, str]:
    """Ensure the vertical's data source exists in Mongo; return (datasource_id, query_id).

    Each vertical points at its own warehouse database, so the four coexist in one org.
    """
    name = _datasource_name(vertical)
    settings, params = _settings_and_params(warehouse_type, warehouse_db)
    client: MongoClient = MongoClient(config.MONGO_URI)
    try:
        db = client[config.MONGO_DB]
        org = db.organizations.find_one()
        if org is None:
            raise RuntimeError(
                "No organization in Mongo — complete first-run GrowthBook setup first."
            )
        org_id = org["id"]

        existing = db.datasources.find_one({"organization": org_id, "name": name})
        if existing is not None:
            return existing["id"], _user_id_query(existing["settings"])

        now = dt.datetime.now(dt.UTC)
        ds_id = "ds_" + uuid.uuid4().hex[:13]
        db.datasources.insert_one(
            {
                "_id": ObjectId(),
                "id": ds_id,
                "name": name,
                "description": "Seeded by gbsynth bootstrap.",
                "organization": org_id,
                "dateCreated": now,
                "dateUpdated": now,
                "type": warehouse_type,
                "params": crypto.encrypt(params, config.ENCRYPTION_KEY),
                "projects": [],
                "settings": settings,
                "__v": 0,
            }
        )
        return ds_id, _user_id_query(settings)
    finally:
        client.close()
