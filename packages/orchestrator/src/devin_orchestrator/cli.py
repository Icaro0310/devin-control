"""CLI: devin-orchestrator plan '<spec-json>' [--explain] | schema <name>."""

from __future__ import annotations

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

from devin_orchestrator import registry as plan_registry
from devin_orchestrator.planner import (
    SCOPE_UNIT_MINUTES,
    estimate_cost,
    plan_task,
    plan_task_json,
)

SCHEMA_NAMES = ("spec", "plan")


def _load_spec(spec_json: str) -> dict:
    try:
        return json.loads(spec_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid task spec JSON: {exc}") from exc


def _explain(spec_json: str, cost: bool = False) -> str:
    """Human-readable walkthrough of the plan decision (OR-2)."""
    spec = _load_spec(spec_json)
    plan = plan_task(spec)
    lines = [f"Plan: {plan.workers} worker(s), mode={plan.mode}"]
    if plan.nesting_blocked:
        lines.append("- this session is already inside a worker; nesting is forbidden")
    else:
        lines.append(f"- {plan.rationale}")
    if plan.profiles:
        lines.append(f"- profile(s): {', '.join(sorted(set(plan.profiles)))}")
    if plan.collect:
        lines.append("- collect=true: gather every worker result before reporting")
    for w in plan.warnings:
        lines.append(f"- warning: {w}")
    for c in plan.file_collisions:
        lines.append(f"- collision: units {c['units']} both touch {c['path']}")
    if cost:
        est = estimate_cost(spec, plan)
        lines.append("Cost estimate (planning only — not billing):")
        for u in est["units"]:
            lines.append(f"- unit {u['id']}: ~{u['est_minutes']:g} min")
        lines.append(
            f"- {est['unit_count']} unit(s): ~{est['sequential_minutes']:g} min"
            f" sequential vs ~{est['fanout_minutes']:g} min fan-out"
            f" ({est['planned_workers']} worker lane(s))")
        lines.append(f"- estimated sessions: {est['estimated_sessions']}")
        for a in est["assumptions"]:
            lines.append(f"- assumption: {a}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="devin-orchestrator",
        description="Decide how many background workers a task should fan out to.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    plan_p = sub.add_parser("plan", help="emit a worker plan as JSON")
    group = plan_p.add_mutually_exclusive_group(required=True)
    group.add_argument("spec", nargs="?", help="task spec as a JSON string")
    group.add_argument("--spec-file", type=Path, help="path to a JSON task spec")
    plan_p.add_argument(
        "--explain", action="store_true",
        help="print a human-readable decision walkthrough instead of JSON",
    )
    plan_p.add_argument(
        "--cost-estimate", action="store_true",
        help="add a per-unit duration estimate (minutes; a rough planning "
        "label — sequential vs fan-out critical path, not billing)",
    )
    schema_p = sub.add_parser(
        "schema", help="print the JSON Schema for the spec or the plan")
    schema_p.add_argument("name", choices=SCHEMA_NAMES)
    rec_p = sub.add_parser(
        "record",
        help="OR-3: append a plan's outcome to the local plans.jsonl "
        "registry (local only — not telemetry)")
    rec_p.add_argument("plan", help="the plan JSON to record against")
    rec_p.add_argument("--outcome", required=True,
                       choices=plan_registry.OUTCOMES)
    rec_p.add_argument("--notes", default="")
    rec_p.add_argument("--registry", type=Path,
                       help="override plans.jsonl location")
    rec_p.add_argument(
        "--run-id",
        help="id of the run being recorded; default run-<ts>-<plan_hash>")
    rec_p.add_argument(
        "--session-id",
        help="Devin session id that executed the plan")
    rec_p.add_argument(
        "--duration", type=float, metavar="SECONDS",
        help="total wall-clock run duration in seconds")
    rec_p.add_argument(
        "--started", type=float, metavar="EPOCH",
        help="run start as a Unix timestamp (seconds)")
    rec_p.add_argument(
        "--units", metavar="JSON",
        help='per-unit results: [{"id","status","duration_s"}, ...]')
    hist_p = sub.add_parser(
        "history",
        help="read the local plan registry (counts + records)")
    hist_p.add_argument("--registry", type=Path,
                        help="override plans.jsonl location")
    hist_p.add_argument("--json", action="store_true")
    cal_p = sub.add_parser(
        "calibrate",
        help="recompute duration-estimate defaults from recorded runs "
        "(plans.jsonl + metrics/<run_id>.json)")
    cal_p.add_argument("--registry", type=Path,
                       help="override plans.jsonl location")
    cal_p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "schema":
        name = (
            "spec.schema.json" if args.name == "spec" else "plan.schema.json"
        )
        text = files("devin_orchestrator").joinpath(name).read_text(
            encoding="utf-8")
        print(text, end="" if text.endswith("\n") else "\n")
        return 0

    if args.command == "record":
        reg = args.registry or plan_registry.default_registry_path()
        try:
            plan = json.loads(args.plan)
            units = json.loads(args.units) if args.units else None
            rec = plan_registry.record(
                reg, plan, args.outcome, args.notes,
                run={
                    "run_id": args.run_id,
                    "session_id": args.session_id,
                    "started": args.started,
                    "duration_s": args.duration,
                    "units": units,
                })
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        out = rec.to_dict()
        if rec.metrics_path:
            out["metrics_path"] = rec.metrics_path
        print(json.dumps(out))
        if rec.metrics_error:
            print(f"warning: {rec.metrics_error}", file=sys.stderr)
        return 0

    if args.command == "history":
        reg = args.registry or plan_registry.default_registry_path()
        summary = plan_registry.summarize(reg)
        if args.json:
            summary["records"] = list(plan_registry.iter_records(reg))
            print(json.dumps(summary, indent=2))
        else:
            print(f"{summary['total']} recorded plan(s): "
                  + ", ".join(f"{k}={v}" for k, v in
                              sorted(summary["by_outcome"].items())
                              ) if summary["by_outcome"] else "none")
            print(f"({summary['note']})")
        return 0

    if args.command == "calibrate":
        reg = args.registry or plan_registry.default_registry_path()
        report = plan_registry.calibrate(reg)
        report["current_scope_defaults"] = dict(SCOPE_UNIT_MINUTES)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"{report['runs']} recorded run(s), "
                  f"{report['units_observed']} unit duration(s) observed")
            suggested = report["suggested_unit_minutes"]
            if suggested:
                print(f"suggested unit minutes: "
                      f"mean={suggested['mean_minutes']:g} "
                      f"p50={suggested['p50_minutes']:g}")
            else:
                print("no durations recorded yet — keep the current "
                      "scope defaults")
            print(f"({report['note']})")
        return 0

    raw = args.spec if args.spec is not None else args.spec_file.read_text(
        encoding="utf-8")
    try:
        if args.explain:
            out = _explain(raw, cost=args.cost_estimate)
        elif args.cost_estimate:
            spec = _load_spec(raw)
            plan = plan_task(spec)
            doc = plan.to_dict()
            doc["cost_estimate"] = estimate_cost(spec, plan)
            out = json.dumps(doc, indent=2)
        else:
            out = plan_task_json(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
