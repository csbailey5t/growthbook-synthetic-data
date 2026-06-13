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

DATASOURCE_NAME = "gbsynth SaaS Warehouse"

# Segment auto-config shape, captured from a real UI-created data source in Phase 0.
_SEGMENT_SETTINGS = {
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
                "query": "",  # set below
            },
            {
                "id": "user_id",
                "userIdType": "user_id",
                "dimensions": ["source", "medium", "device", "browser"],
                "name": "Logged-in Users",
                "description": "",
                "query": "",  # set below
            },
        ],
        "identityJoins": [
            {
                "ids": ["user_id", "anonymous_id"],
                "query": "SELECT\n  user_id,\n  anonymous_id\nFROM\n  public.identifies",
            }
        ],
    },
    "schemaOptions": {"exposureTableName": "experiment_viewed"},
}


def _exposure_sql(id_col: str) -> str:
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
        "    ELSE 'Other' END\n  ) as browser\nFROM\n  public.experiment_viewed\n"
        f"WHERE\n  {id_col} is not null"
    )


_SEGMENT_SETTINGS["queries"]["exposure"][0]["query"] = _exposure_sql("anonymous_id")
_SEGMENT_SETTINGS["queries"]["exposure"][1]["query"] = _exposure_sql("user_id")


def _user_id_query(settings: dict) -> str:
    """The assignment query keyed on user_id (matches the metrics' identifier)."""
    for q in settings["queries"]["exposure"]:
        if q["userIdType"] == "user_id":
            return q["id"]
    return settings["queries"]["exposure"][0]["id"]


def bootstrap_datasource() -> tuple[str, str]:
    """Ensure the SaaS data source exists in Mongo; return (datasource_id, query_id)."""
    client: MongoClient = MongoClient(config.MONGO_URI)
    try:
        db = client[config.MONGO_DB]
        org = db.organizations.find_one()
        if org is None:
            raise RuntimeError(
                "No organization in Mongo — complete first-run GrowthBook setup first."
            )
        org_id = org["id"]

        existing = db.datasources.find_one({"organization": org_id, "name": DATASOURCE_NAME})
        if existing is not None:
            return existing["id"], _user_id_query(existing["settings"])

        now = dt.datetime.now(dt.UTC)
        ds_id = "ds_" + uuid.uuid4().hex[:13]
        db.datasources.insert_one(
            {
                "_id": ObjectId(),
                "id": ds_id,
                "name": DATASOURCE_NAME,
                "description": "Seeded by gbsynth bootstrap (Phase 2).",
                "organization": org_id,
                "dateCreated": now,
                "dateUpdated": now,
                "type": "postgres",
                "params": crypto.encrypt(
                    json.dumps(config.DATASOURCE_PARAMS), config.ENCRYPTION_KEY
                ),
                "projects": [],
                "settings": _SEGMENT_SETTINGS,
                "__v": 0,
            }
        )
        return ds_id, _user_id_query(_SEGMENT_SETTINGS)
    finally:
        client.close()
