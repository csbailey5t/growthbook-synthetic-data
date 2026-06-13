"""Provisioner config, loaded from the repo .env (gitignored) + environment.

Holds the secrets the provisioner needs: GrowthBook API, Mongo (for bootstrap), the
ENCRYPTION_KEY (must match the running app), and the warehouse connection. The data-source
params use the host GrowthBook sees on its own network ("postgres"), which differs from the
host the loader uses from the developer's machine ("localhost").
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

GB_API_HOST = os.environ.get("GB_API_HOST", "http://localhost:3100")
GB_API_KEY = os.environ.get("GB_API_KEY", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

_MONGO_USER = os.environ.get("MONGO_USER", "root")
_MONGO_PASSWORD = os.environ.get("MONGO_PASSWORD", "password")
MONGO_URI = os.environ.get(
    "MONGO_URI",
    f"mongodb://{_MONGO_USER}:{_MONGO_PASSWORD}@localhost:27017/growthbook?authSource=admin",
)
MONGO_DB = "growthbook"

# Each vertical gets its own warehouse database so all four can coexist in one org with
# identically-named tables (experiment_viewed/tracks/...). The default compose database
# ("warehouse") is the maintenance db used to create the per-vertical ones.
PG_USER = os.environ.get("POSTGRES_USER", "gbsynth")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "gbsynth")
PG_INTERNAL_HOST = os.environ.get("POSTGRES_INTERNAL_HOST", "postgres")  # as GrowthBook sees it
ADMIN_DB = os.environ.get("POSTGRES_DB", "warehouse")


def loader_dsn(db: str) -> str:
    """Warehouse DSN as the developer's machine reaches it (loader runs from the host)."""
    return f"host=localhost port=5432 dbname={db} user={PG_USER} password={PG_PASSWORD}"


def admin_dsn() -> str:
    return loader_dsn(ADMIN_DB)


def datasource_params(db: str) -> dict:
    """Connection params GrowthBook uses (over the compose network); bootstrap encrypts these."""
    return {
        "host": PG_INTERNAL_HOST,
        "port": 5432,
        "database": db,
        "user": PG_USER,
        "password": PG_PASSWORD,
        "defaultSchema": "public",
    }
