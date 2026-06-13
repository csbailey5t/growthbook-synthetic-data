"""In-memory dataset container: the warehouse tables a generation run produces."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from gbsynth.core.stories import StoryOutcome


@dataclass(slots=True)
class Table:
    name: str
    columns: list[tuple[str, str]]  # (column_name, postgres_type)
    rows: list[tuple]
    # Timestamp column used for delete-by-partition idempotent loads. None => dimension
    # table, full-replaced on load.
    partition_column: str | None = None

    @property
    def column_names(self) -> list[str]:
        return [c for c, _ in self.columns]


@dataclass(slots=True)
class Dataset:
    tables: list[Table]
    outcomes: list[StoryOutcome]
    window_start: dt.datetime
    window_end: dt.datetime
    extra: dict = field(default_factory=dict)
