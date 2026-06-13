"""Postgres loader: CREATE TABLE + COPY bulk load with idempotent, date-partitioned writes.

Re-loading the same generated window must not duplicate rows (PLAN.md:225-227). Fact tables
carry a `partition_column`: we delete rows in the loaded window and re-insert. Dimension
tables (no partition column) are truncated and replaced. Because generation is
deterministic, a re-run reconciles to the identical state.
"""

from __future__ import annotations

import os

import psycopg
from psycopg import sql

from gbsynth.dataset import Dataset, Table


def default_dsn() -> str:
    """Connection string for the local compose warehouse (override via env vars)."""
    return (
        f"host={os.environ.get('POSTGRES_HOST', 'localhost')} "
        f"port={os.environ.get('POSTGRES_PORT', '5432')} "
        f"dbname={os.environ.get('POSTGRES_DB', 'warehouse')} "
        f"user={os.environ.get('POSTGRES_USER', 'gbsynth')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', 'gbsynth')}"
    )


def _create_table(cur: psycopg.Cursor, table: Table) -> None:
    cols = ", ".join(f'"{name}" {pgtype}' for name, pgtype in table.columns)
    # DDL built from our own schema constants (no user input); the dynamic string is safe.
    cur.execute(f'CREATE TABLE IF NOT EXISTS "{table.name}" ({cols})')  # ty: ignore[no-matching-overload]


def _clear(cur: psycopg.Cursor, table: Table, window_start) -> None:
    if table.partition_column:
        cur.execute(
            sql.SQL("DELETE FROM {} WHERE {} >= %s").format(
                sql.Identifier(table.name), sql.Identifier(table.partition_column)
            ),
            (window_start,),
        )
    else:
        cur.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(table.name)))


def _copy_rows(cur: psycopg.Cursor, table: Table) -> None:
    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table.name),
        sql.SQL(", ").join(sql.Identifier(c) for c in table.column_names),
    )
    with cur.copy(stmt) as copy:
        for row in table.rows:
            copy.write_row(row)


def load_dataset(dataset: Dataset, dsn: str | None = None) -> dict[str, int]:
    """Load all tables; returns {table_name: row_count}."""
    counts: dict[str, int] = {}
    with psycopg.connect(dsn or default_dsn()) as conn, conn.cursor() as cur:
        for table in dataset.tables:
            _create_table(cur, table)
            _clear(cur, table, dataset.window_start)
            _copy_rows(cur, table)
            counts[table.name] = len(table.rows)
        conn.commit()
    return counts
