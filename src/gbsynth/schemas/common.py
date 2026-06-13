"""Cross-vertical entity tables: users + the flat `tracks` event table.

Numeric value columns are `double precision`, not `numeric` — the Phase 0 finding: the
Postgres driver returns NUMERIC as strings, which GrowthBook's column detection types as
"string" and then refuses to `sum` for mean metrics.
"""

from __future__ import annotations

from gbsynth.core.population import User
from gbsynth.core.sessions import Event
from gbsynth.dataset import Table

USERS_TABLE = "users"
TRACKS_TABLE = "tracks"


def users_table(users: list[User]) -> Table:
    rows = [
        (u.user_id, u.anonymous_id, u.persona, u.country, u.signup_at, u.propensity) for u in users
    ]
    return Table(
        name=USERS_TABLE,
        columns=[
            ("user_id", "text"),
            ("anonymous_id", "text"),
            ("persona", "text"),
            ("country", "text"),
            ("signup_at", "timestamptz"),
            ("propensity", "double precision"),
        ],
        rows=rows,
        partition_column=None,  # dimension table: full replace on load
    )


def tracks_table(events: list[Event]) -> Table:
    rows = [(e.user_id, e.anonymous_id, e.event, e.received_at, e.value) for e in events]
    return Table(
        name=TRACKS_TABLE,
        columns=[
            ("user_id", "text"),
            ("anonymous_id", "text"),
            ("event", "text"),
            ("received_at", "timestamptz"),
            ("value", "double precision"),
        ],
        rows=rows,
        partition_column="received_at",
    )
