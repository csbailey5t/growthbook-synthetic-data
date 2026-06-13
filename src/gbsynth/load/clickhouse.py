"""ClickHouse loader: DDL + insert via clickhouse-driver (native protocol).

ClickHouse specifics (PLAN.md:82-84): DateTime64 timestamps, MergeTree ORDER BY user_id,
consistent String id types, Nullable only where the data needs it (the tracks `value`
column is null for conversion events).

Uses clickhouse-driver over the native port (9000) rather than clickhouse-connect, which
requires pandas>=2 and conflicts with gbstats's pandas<2 pin. GrowthBook's own connection
to ClickHouse is over HTTP (8123) and independent of this loader.

Idempotency is full-replace (DROP + CREATE + INSERT) — generation is deterministic, so a
re-run reconciles to the identical state. Partition-level delete arrives with Phase 4.
"""

from __future__ import annotations

import datetime as dt
import os

from clickhouse_driver import Client

from gbsynth.dataset import Dataset, Table

_TYPE_MAP = {
    "text": "String",
    "timestamptz": "DateTime64(3, 'UTC')",
    "smallint": "Int8",
    "double precision": "Float64",
}


def default_client() -> Client:
    return Client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "9000")),
        user=os.environ.get("CLICKHOUSE_USER", "gbsynth"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", "gbsynth"),
        database=os.environ.get("CLICKHOUSE_DB", "warehouse"),
    )


def _ch_type(pg_type: str, nullable: bool) -> str:
    base = _TYPE_MAP[pg_type]
    return f"Nullable({base})" if nullable else base


def _naive_utc(value):
    """clickhouse-driver wants naive datetimes for a UTC column."""
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.UTC).replace(tzinfo=None)
    return value


def _create(client: Client, table: Table) -> dict[str, bool]:
    nullable = {
        name: any(row[i] is None for row in table.rows) for i, (name, _) in enumerate(table.columns)
    }
    cols = ", ".join(f"`{n}` {_ch_type(t, nullable[n])}" for n, t in table.columns)
    client.execute(f"DROP TABLE IF EXISTS `{table.name}`")
    client.execute(f"CREATE TABLE `{table.name}` ({cols}) ENGINE = MergeTree ORDER BY user_id")
    return nullable


def load_dataset(dataset: Dataset, client: Client | None = None) -> dict[str, int]:
    """Load all tables into ClickHouse; returns {table_name: row_count}."""
    client = client or default_client()
    counts: dict[str, int] = {}
    for table in dataset.tables:
        _create(client, table)
        if table.rows:
            rows = [tuple(_naive_utc(v) for v in row) for row in table.rows]
            cols = ", ".join(f"`{c}`" for c in table.column_names)
            client.execute(f"INSERT INTO `{table.name}` ({cols}) VALUES", rows)
        counts[table.name] = len(table.rows)
    return counts
