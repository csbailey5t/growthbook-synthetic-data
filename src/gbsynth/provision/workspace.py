"""Workspace provisioning: the GrowthBook project the vertical's objects live under.

Thin slice: just the project (the minimum metrics/experiments need). Environments,
attributes, saved groups, and SDK connections are deferred to the polish follow-up.
"""

from __future__ import annotations

from gbsynth.provision.client import GBClient


def ensure_project(client: GBClient, name: str) -> str:
    """Create the project, or reuse it if one with this name already exists."""
    for p in client.get("/projects").get("projects", []):
        if p.get("name") == name:
            return p["id"]
    res = client.post("/projects", {"name": name, "description": "gbsynth synthetic demo."})
    return res["project"]["id"]
