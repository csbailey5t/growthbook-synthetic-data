"""Segment-flavored output tables: experiment_viewed + identifies.

Column names match what GrowthBook's Segment auto-configuration expects (incl. the
`context_*` columns that the auto-generated assignment SQL derives dimensions from —
the Phase 0 finding), so a Segment data source "just works" with no hand-written SQL.
"""

from __future__ import annotations

from gbsynth.core.experiments import Exposure
from gbsynth.core.population import User
from gbsynth.dataset import Table

EXPOSURE_TABLE = "experiment_viewed"
IDENTIFIES_TABLE = "identifies"


def experiment_viewed(exposures: list[Exposure], experiment_id: str) -> Table:
    rows = [
        (
            e.user.user_id,
            e.user.anonymous_id,
            e.exposed_at,
            experiment_id,
            e.variation,
            e.user.source,
            e.user.medium,
            e.user.user_agent,
        )
        for e in exposures
    ]
    return Table(
        name=EXPOSURE_TABLE,
        columns=[
            ("user_id", "text"),
            ("anonymous_id", "text"),
            ("received_at", "timestamptz"),
            ("experiment_id", "text"),
            ("variation_id", "smallint"),
            ("context_campaign_source", "text"),
            ("context_campaign_medium", "text"),
            ("context_user_agent", "text"),
        ],
        rows=rows,
        partition_column="received_at",
    )


def identifies(users: list[User]) -> Table:
    rows = [(u.user_id, u.anonymous_id, u.signup_at) for u in users]
    return Table(
        name=IDENTIFIES_TABLE,
        columns=[
            ("user_id", "text"),
            ("anonymous_id", "text"),
            ("received_at", "timestamptz"),
        ],
        rows=rows,
        partition_column="received_at",
    )
