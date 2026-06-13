"""Feature-flag provisioning: a representative catalog per vertical.

Covers the rule types a demo needs (PLAN.md:265): kill switches, a percentage rollout, a
targeting rule, a stale flag (cleanup candidate), and an experiment-ref flag per
hand-scripted story that links the flag to its experiment. Created via v1 POST /v1/features
with environment rules. Idempotent: skips flags that already exist.

Vertical specs may also declare custom flags (e.g. the AI vertical's JSON model-config
flags) in spec.flags; those are provisioned alongside the generated catalog.
"""

from __future__ import annotations

import json

from gbsynth.provision.client import GBClient
from gbsynth.provision.experiments import _find
from gbsynth.spec import VerticalSpec

ENV = "production"
_EU = json.dumps({"country": {"$in": ["GB", "DE", "FR"]}})


def _feature(
    fid: str,
    project: str,
    description: str,
    value_type: str = "boolean",
    default: str = "false",
    enabled: bool = True,
    rules: list[dict] | None = None,
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": fid,
        "owner": "",
        "description": description,
        "valueType": value_type,
        "defaultValue": default,
        "project": project,
        "tags": tags or [],
        "environments": {ENV: {"enabled": enabled, "rules": rules or []}},
    }


def _generic_catalog(spec: VerticalSpec, project: str) -> list[dict]:
    v = spec.name
    return [
        _feature(
            f"{v}-new-navigation",
            project,
            "New top navigation (kill switch).",
            default="true",
            tags=["killswitch"],
        ),
        _feature(
            f"{v}-maintenance-mode",
            project,
            "Maintenance banner (kill switch, off).",
            enabled=False,
            tags=["killswitch"],
        ),
        _feature(
            f"{v}-beta-redesign",
            project,
            "Redesign, ramped to 25%.",
            rules=[
                {
                    "type": "rollout",
                    "description": "25% ramp",
                    "value": "true",
                    "coverage": 0.25,
                    "hashAttribute": "user_id",
                    "enabled": True,
                }
            ],
            tags=["rollout"],
        ),
        _feature(
            f"{v}-eu-cookie-banner",
            project,
            "EU-only cookie banner (targeting).",
            rules=[
                {
                    "type": "force",
                    "description": "EU countries",
                    "value": "true",
                    "condition": _EU,
                    "enabled": True,
                }
            ],
            tags=["targeting"],
        ),
        _feature(
            f"{v}-legacy-export",
            project,
            "Legacy export — stale, cleanup candidate.",
            enabled=False,
            tags=["stale"],
        ),
    ]


def _experiment_ref_flags(client: GBClient, spec: VerticalSpec, project: str) -> list[dict]:
    flags: list[dict] = []
    for story in spec.stories:
        exp = _find(client, story.key)
        if exp is None:
            continue
        full = client.get(f"/experiments/{exp['id']}")["experiment"]
        var_ids = [v["variationId"] for v in full["variations"]]
        if len(var_ids) < 2:
            continue
        # Boolean flag: control serves false, treatment serves true.
        variations = [{"variationId": var_ids[0], "value": "false"}]
        variations += [{"variationId": vid, "value": "true"} for vid in var_ids[1:]]
        flags.append(
            _feature(
                f"{story.key}-flag",
                project,
                f"Feature gate driven by the '{story.name}' experiment.",
                rules=[
                    {
                        "type": "experiment-ref",
                        "description": story.name,
                        "enabled": True,
                        "experimentId": exp["id"],
                        "variations": variations,
                    }
                ],
                tags=["experiment"],
            )
        )
    return flags


def _existing_feature_ids(client: GBClient) -> set[str]:
    ids: set[str] = set()
    offset = 0
    while True:
        page = client.get("/features", params={"limit": 100, "offset": offset})
        ids.update(f["id"] for f in page.get("features", []))
        if not page.get("hasMore"):
            return ids
        offset = page.get("nextOffset") or offset + 100


def provision_features(client: GBClient, project_id: str, spec: VerticalSpec) -> int:
    """Create the vertical's flag catalog (idempotent). Returns the count created."""
    catalog = _generic_catalog(spec, project_id)
    catalog += [
        _feature(f.id, project_id, f.description, f.value_type, f.default_value, tags=f.tags)
        for f in spec.flags
    ]
    catalog += _experiment_ref_flags(client, spec, project_id)

    existing = _existing_feature_ids(client)
    created = 0
    for flag in catalog:
        if flag["id"] in existing:
            continue
        client.post("/features", flag)
        created += 1
    return created
