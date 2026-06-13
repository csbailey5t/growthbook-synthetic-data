"""DB-free unit tests for the ClickHouse loader's type mapping and value coercion."""

from __future__ import annotations

import datetime as dt

from gbsynth.load.clickhouse import _ch_type, _naive_utc


def test_type_mapping() -> None:
    assert _ch_type("text", False) == "String"
    assert _ch_type("timestamptz", False) == "DateTime64(3, 'UTC')"
    assert _ch_type("smallint", False) == "Int8"
    assert _ch_type("double precision", False) == "Float64"


def test_nullable_wrapping() -> None:
    # The tracks `value` column is null for conversion events -> Nullable.
    assert _ch_type("double precision", True) == "Nullable(Float64)"


def test_naive_utc_strips_timezone() -> None:
    aware = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)
    naive = _naive_utc(aware)
    assert naive.tzinfo is None
    assert naive == dt.datetime(2026, 1, 2, 3, 4, 5)
    assert _naive_utc("not-a-date") == "not-a-date"  # passthrough
