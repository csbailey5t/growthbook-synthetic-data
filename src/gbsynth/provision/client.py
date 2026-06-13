"""Thin typed GrowthBook REST client.

Graduated from the Phase 0 spike with the 60 req/min throttle the API enforces
(PLAN.md:60): a minimum spacing between calls plus Retry-After-aware backoff on 429.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

# 60 req/min => one request per second; a little headroom keeps us safely under.
_MIN_INTERVAL = 1.05


class GBError(RuntimeError):
    pass


class GBClient:
    def __init__(self, host: str, key: str) -> None:
        if not key:
            raise GBError("GB_API_KEY is empty — set it in .env (Settings -> API Keys).")
        self._client = httpx.Client(
            base_url=f"{host}/api/v1",
            headers={"Authorization": f"Bearer {key}"},
            timeout=60.0,
        )
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_request = time.monotonic()

    def request(self, method: str, path: str, **kwargs: Any) -> dict:
        for attempt in range(5):
            self._throttle()
            resp = self._client.request(method, path, **kwargs)
            if resp.status_code == 429:
                time.sleep(float(resp.headers.get("Retry-After", 2**attempt)))
                continue
            if resp.status_code >= 400:
                raise GBError(f"{method} {path} -> {resp.status_code}: {resp.text}")
            return resp.json()
        raise GBError(f"{method} {path} -> rate-limited after retries")

    def get(self, path: str, **kw: Any) -> dict:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload: dict | None = None) -> dict:
        return self.request("POST", path, json=payload or {})

    def put(self, path: str, payload: dict) -> dict:
        return self.request("PUT", path, json=payload)
