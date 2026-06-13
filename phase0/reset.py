"""Golden-snapshot reset for the GrowthBook Mongo database (Phase 0 proof).

Demo activity (flags, experiments, edits an AE creates) lives only in GrowthBook's Mongo
db — never in the warehouse, whose credentials are read-only (PLAN.md:193-199). So a
clean revert is just mongodump/mongorestore of that one database.

Streams a gzip archive in/out through `docker compose exec` so no Mongo tools are needed
on the host. The full Phase 4 version adds dump hygiene (excluding the recomputable query
cache), versioned retention, and a Tigris bucket — here we keep it faithful and simple.

Usage:
  uv run python -m phase0.reset snapshot [name]   # dump golden state -> snapshots/<name>.gz
  uv run python -m phase0.reset restore [name]     # restore (defaults to newest snapshot)
  uv run python -m phase0.reset list
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from phase0 import config

SNAP_DIR = config.REPO_ROOT / "snapshots"
MONGO_DB = "growthbook"
MONGO_URI = (
    f"mongodb://{config.ENV['MONGO_USER']}:{config.ENV['MONGO_PASSWORD']}"
    f"@localhost:27017/{MONGO_DB}?authSource=admin"
)


def _exec(tool_args: list[str], *, stdin: bytes | None = None) -> bytes:
    """Run a command inside the mongo container, returning its stdout."""
    cmd = ["docker", "compose", "exec", "-T", "mongo", *tool_args]
    proc = subprocess.run(cmd, input=stdin, capture_output=True, cwd=config.REPO_ROOT)
    if proc.returncode != 0:
        sys.exit(f"{' '.join(tool_args[:1])} failed:\n{proc.stderr.decode(errors='replace')}")
    return proc.stdout


def snapshot(name: str) -> Path:
    SNAP_DIR.mkdir(exist_ok=True)
    target = SNAP_DIR / f"{name}.archive.gz"
    archive = _exec(["mongodump", f"--uri={MONGO_URI}", "--archive", "--gzip"])
    target.write_bytes(archive)
    print(f"Snapshot written: {target}  ({len(archive):,} bytes)")
    return target


def _newest() -> Path:
    snaps = sorted(SNAP_DIR.glob("*.archive.gz"), key=lambda p: p.stat().st_mtime)
    if not snaps:
        sys.exit("No snapshots found — run `reset snapshot` first.")
    return snaps[-1]


def restore(name: str | None) -> None:
    src = (SNAP_DIR / f"{name}.archive.gz") if name else _newest()
    if not src.exists():
        sys.exit(f"Snapshot not found: {src}")
    _exec(
        ["mongorestore", f"--uri={MONGO_URI}", "--archive", "--gzip", "--drop"],
        stdin=src.read_bytes(),
    )
    print(f"Restored from: {src}")


def list_snapshots() -> None:
    SNAP_DIR.mkdir(exist_ok=True)
    snaps = sorted(SNAP_DIR.glob("*.archive.gz"), key=lambda p: p.stat().st_mtime)
    if not snaps:
        print("No snapshots yet.")
        return
    for p in snaps:
        print(f"  {p.name}  ({p.stat().st_size:,} bytes)")


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    arg = args[1] if len(args) > 1 else None
    if cmd == "snapshot":
        snapshot(arg or "golden")
    elif cmd == "restore":
        restore(arg)
    elif cmd == "list":
        list_snapshots()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
