"""gbsynth command-line interface.

  gbsynth generate [vertical]   build the dataset, verify story outcomes (no DB)
  gbsynth load     [vertical]   build + load into Postgres

Phase 1 is API/Fly-independent: verification is offline via gbstats. Both commands exit
non-zero if any scripted story's primary metric misses its target band, so they double as
a check in CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gbsynth.build import build_dataset
from gbsynth.dataset import Dataset
from gbsynth.load.postgres import load_dataset
from gbsynth.spec import VerticalSpec


def _spec_path(args: argparse.Namespace) -> str:
    if args.spec:
        return args.spec
    return str(Path("config") / "verticals" / f"{args.vertical}.yaml")


def _report(dataset: Dataset) -> bool:
    """Print per-story outcomes; return True if all primary metrics are in band."""
    all_ok = True
    for r in dataset.extra["story_results"]:
        print(f"\nStory '{r['name']}' ({r['key']}) [{r['status']}]")
        print(
            f"  exposed: {r['n_exposed']:,} (~{r['planned_n']:,}/arm)  "
            f"solved lift: {r['resolved_lift']:+.1%}  "
            f"expected chance-to-win: {r['expected_ctw']:.1%}"
        )
        for o in r["outcomes"]:
            tag = " [primary]" if o.is_primary else ""
            status = "" if o.in_band else "  <-- OUT OF BAND"
            print(
                f"    {o.metric_name:<18}{tag:<10} "
                f"control={o.control_mean:.4g}  treatment={o.treatment_mean:.4g}  "
                f"lift={o.lift:+.1%}  CTW={o.chance_to_win:.1%}  p={o.p_value:.3f}{status}"
            )
            if o.is_primary and not o.in_band:
                all_ok = False
    return all_ok


def _build(args: argparse.Namespace) -> Dataset:
    spec = VerticalSpec.from_yaml(_spec_path(args))
    print(f"Generating '{spec.name}' — {spec.scale.n_users:,} users over {spec.scale.months}mo")
    return build_dataset(spec)


def cmd_generate(args: argparse.Namespace) -> int:
    dataset = _build(args)
    ok = _report(dataset)
    print("\nTables:")
    for t in dataset.tables:
        print(f"  {t.name:<18} {len(t.rows):,} rows")
    print("\n" + ("PASS: all stories landed in band." if ok else "FAIL: a story missed its band."))
    return 0 if ok else 1


def cmd_load(args: argparse.Namespace) -> int:
    dataset = _build(args)
    ok = _report(dataset)
    counts = load_dataset(dataset, args.dsn)
    print("\nLoaded into Postgres:")
    for name, n in counts.items():
        print(f"  {name:<18} {n:,} rows")
    if not ok:
        print("\nWARNING: a story missed its target band (loaded anyway).")
    return 0 if ok else 1


def cmd_provision(args: argparse.Namespace) -> int:
    from gbsynth.provision.provisioner import provision

    spec = VerticalSpec.from_yaml(_spec_path(args))
    print(f"Provisioning '{spec.name}' into GrowthBook...")
    report = provision(spec)
    print(f"\nProject {report.project_id}  ·  data source {report.datasource_id}")
    print("Loaded:", ", ".join(f"{k}={v:,}" for k, v in report.loaded.items()))
    print("\nExperiments (live snapshot vs gbstats prediction):")
    for e in report.experiments:
        status = "OK" if e.ok else "MISMATCH"
        print(
            f"  {e.key:<26} [{e.status:<7}] predicted CTW={e.expected_ctw:.1%}  "
            f"live={e.live_ctw:.1%}  srm={e.srm:.3f}  {status}"
        )
    print(
        "\n"
        + (
            "PASS: live results match the scripted outcomes."
            if report.ok
            else "FAIL: a live result diverged from its prediction."
        )
    )
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gbsynth", description="Synthetic GrowthBook demo data")
    sub = parser.add_subparsers(dest="command", required=True)

    commands = (("generate", cmd_generate), ("load", cmd_load), ("provision", cmd_provision))
    for name, func in commands:
        p = sub.add_parser(name, help=func.__doc__)
        p.add_argument("vertical", nargs="?", default="saas", help="vertical name (default: saas)")
        p.add_argument("--spec", help="explicit path to a vertical YAML (overrides vertical)")
        if name == "load":
            p.add_argument("--dsn", help="Postgres DSN (default: local compose warehouse)")
        p.set_defaults(func=func)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
