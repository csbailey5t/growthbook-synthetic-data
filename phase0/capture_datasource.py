"""Capture the UI-created data-source document from GrowthBook's Mongo.

This is the key Phase 0 *artifact*: data sources are read-only via the REST API
(PLAN.md:52-56), so the only way to make the org bootstrappable from zero is to seed the
data-source document directly in Mongo. To do that faithfully we first capture a real one
created through the UI — including the exact `params` shape (encrypted with ENCRYPTION_KEY)
and the auto-generated `settings.queries` — as the template Phase 2's bootstrap.py replays.

Writes extended JSON (EJSON) so encrypted Binary fields and ObjectIds survive round-trip.

Run (after creating the data source in the UI):
  uv run python -m phase0.capture_datasource
"""

from __future__ import annotations

import subprocess
import sys

from phase0 import config

OUT = config.REPO_ROOT / "phase0" / "captured_datasource.json"
MONGO_URI = (
    f"mongodb://{config.ENV['MONGO_USER']}:{config.ENV['MONGO_PASSWORD']}"
    f"@localhost:27017/growthbook?authSource=admin"
)
# EJSON preserves Binary (the encrypted credentials) and ObjectIds; plain JSON would not.
EVAL = "print(EJSON.stringify(db.datasources.find().toArray(), null, 2))"


def main() -> None:
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "mongo",
        "mongosh",
        MONGO_URI,
        "--quiet",
        "--eval",
        EVAL,
    ]
    proc = subprocess.run(cmd, capture_output=True, cwd=config.REPO_ROOT)
    if proc.returncode != 0:
        sys.exit(f"mongosh failed:\n{proc.stderr.decode(errors='replace')}")

    out = proc.stdout.decode().strip()
    if out in ("", "[]"):
        sys.exit(
            "No data sources found in Mongo. Create the Postgres+Segment data source "
            "in the GrowthBook UI first, then re-run."
        )

    OUT.write_text(out + "\n")
    print(f"Captured data-source document(s) -> {OUT}")
    print(
        "This is the bootstrap.py template. Note the encrypted `params` Binary and the "
        "auto-generated `settings.queries` — Phase 2 reproduces both."
    )


if __name__ == "__main__":
    main()
