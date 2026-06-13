"""Tear down a single vertical's objects so it can be rebuilt (PLAN.md:305).

Surgical alternative to a full `reset` (which reverts the whole org): removes just one
vertical's GrowthBook objects (experiments, features, metrics, fact table, data source,
project) via Mongo — experiments have no REST delete, so Mongo is the only complete path —
and drops its warehouse database(s). Idempotent. Follow with `gbsynth provision <vertical>`
to rebuild.

Destructive by design; scoped strictly to objects named/keyed for the given vertical.
"""

from __future__ import annotations

import psycopg
from pymongo import MongoClient

from gbsynth.provision import config


def _drop_postgres_db(db: str) -> bool:
    with psycopg.connect(config.admin_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        if cur.fetchone() is None:
            return False
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (db,)
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{db}"')  # ty: ignore[no-matching-overload]
        return True


def _drop_clickhouse_db(db: str) -> bool:
    try:
        from gbsynth.load.clickhouse import _conn
    except Exception:
        return False
    try:
        admin = _conn("default")
        admin.execute(f"DROP DATABASE IF EXISTS `{db}`")
        admin.disconnect()
        return True
    except Exception:
        return False


def cleanup(vertical: str, drop_warehouse: bool = True) -> dict[str, int]:
    """Remove the vertical's GrowthBook objects and warehouse databases. Returns counts."""
    prefix = f"^{vertical}-"
    client: MongoClient = MongoClient(config.MONGO_URI)
    removed: dict[str, int] = {}
    try:
        db = client[config.MONGO_DB]
        org = db.organizations.find_one()
        org_id = org["id"] if org else None
        scope = {"organization": org_id} if org_id else {}

        removed["experiments"] = db.experiments.delete_many(
            {**scope, "trackingKey": {"$regex": prefix}}
        ).deleted_count
        removed["features"] = db.features.delete_many(
            {**scope, "id": {"$regex": prefix}}
        ).deleted_count
        removed["featurerevisions"] = db.featurerevisions.delete_many(
            {**scope, "featureId": {"$regex": prefix}}
        ).deleted_count
        removed["factmetrics"] = db.factmetrics.delete_many(
            {**scope, "id": {"$regex": f"^fact__{vertical}_"}}
        ).deleted_count
        removed["facttables"] = db.facttables.delete_many(
            {**scope, "id": f"ft_{vertical}_tracks"}
        ).deleted_count
        removed["datasources"] = db.datasources.delete_many(
            {**scope, "name": f"gbsynth {vertical} Warehouse"}
        ).deleted_count
        removed["projects"] = db.projects.delete_many(
            {**scope, "name": f"gbsynth: {vertical}"}
        ).deleted_count
    finally:
        client.close()

    if drop_warehouse:
        removed["postgres_db_dropped"] = int(_drop_postgres_db(vertical))
        removed["clickhouse_db_dropped"] = int(_drop_clickhouse_db(vertical))
    return removed
