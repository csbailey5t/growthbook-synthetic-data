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

# Warehouse as the developer's machine reaches it (loader runs from the host).
WAREHOUSE_DB = os.environ.get("POSTGRES_DB", "warehouse")
LOADER_DSN = (
    f"host=localhost port=5432 dbname={WAREHOUSE_DB} "
    f"user={os.environ.get('POSTGRES_USER', 'gbsynth')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'gbsynth')}"
)

# Warehouse as GrowthBook reaches it (over the compose network). These params are what
# bootstrap.py encrypts into the data-source document.
DATASOURCE_PARAMS = {
    "host": os.environ.get("POSTGRES_INTERNAL_HOST", "postgres"),
    "port": 5432,
    "database": WAREHOUSE_DB,
    "user": os.environ.get("POSTGRES_USER", "gbsynth"),
    "password": os.environ.get("POSTGRES_PASSWORD", "gbsynth"),
    "defaultSchema": "public",
}
