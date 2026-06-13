"""Golden-snapshot reset of GrowthBook's Mongo (graduated from the Phase 0 spike).

Demo activity (flags, experiments, edits) lives only in Mongo — the warehouse credentials
are read-only — so reverting to a clean state is mongodump/mongorestore of that one
database (PLAN.md:188-215). Streams a gzip archive through `docker compose exec` so no
Mongo tools are needed on the host.

Defaults to a full dump so a restored workspace shows results immediately. `--exclude-cache`
drops the recomputable query cache (PLAN.md:251-253) for when dumps grow large; results are
then rebuilt by the next snapshot trigger. Keeps the most recent RETENTION snapshots.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from gbsynth.provision import config

SNAP_DIR = Path("snapshots")
RETENTION = 14
# Recomputable query cache (raw SQL results); experiment results live in experimentsnapshots
# and are kept so a restore is demo-ready.
_CACHE_COLLECTIONS = ["queries", "sqlresultchunks", "experimentsnapshotanalysischunks"]


def _exec(tool_args: list[str], *, stdin: bytes | None = None) -> bytes:
    cmd = ["docker", "compose", "exec", "-T", "mongo", *tool_args]
    proc = subprocess.run(cmd, input=stdin, capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"{tool_args[0]} failed:\n{proc.stderr.decode(errors='replace')}")
    return proc.stdout


def snapshot(name: str = "golden", exclude_cache: bool = False) -> Path:
    SNAP_DIR.mkdir(exist_ok=True)
    args = ["mongodump", f"--uri={config.MONGO_URI}", "--archive", "--gzip"]
    if exclude_cache:
        args += [f"--excludeCollection={c}" for c in _CACHE_COLLECTIONS]
    archive = _exec(args)
    target = SNAP_DIR / f"{name}.archive.gz"
    target.write_bytes(archive)
    print(f"Snapshot written: {target} ({len(archive):,} bytes)")
    _prune()
    return target


def _prune() -> None:
    snaps = sorted(SNAP_DIR.glob("*.archive.gz"), key=lambda p: p.stat().st_mtime)
    for old in snaps[:-RETENTION]:
        old.unlink()


def _newest() -> Path:
    snaps = sorted(SNAP_DIR.glob("*.archive.gz"), key=lambda p: p.stat().st_mtime)
    if not snaps:
        sys.exit("No snapshots found — run `gbsynth snapshot` first.")
    return snaps[-1]


def restore(name: str | None = None) -> None:
    src = (SNAP_DIR / f"{name}.archive.gz") if name else _newest()
    if not src.exists():
        sys.exit(f"Snapshot not found: {src}")
    _exec(
        ["mongorestore", f"--uri={config.MONGO_URI}", "--archive", "--gzip", "--drop"],
        stdin=src.read_bytes(),
    )
    print(f"Restored from: {src}")


def list_snapshots() -> list[Path]:
    SNAP_DIR.mkdir(exist_ok=True)
    return sorted(SNAP_DIR.glob("*.archive.gz"), key=lambda p: p.stat().st_mtime)
