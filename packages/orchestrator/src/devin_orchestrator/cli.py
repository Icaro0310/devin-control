"""CLI: devin-orchestrator plan '<spec-json>' [--explain] | schema <name>
| queue <submit|fanout|claim|heartbeat|done|fail|cancel|sweep|collect|status>."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from importlib.resources import files
from pathlib import Path

from devin_orchestrator import emit as plan_emit
from devin_orchestrator import manifests as trigger_manifests
from devin_orchestrator import queue_db
from devin_orchestrator import registry as plan_registry
from devin_orchestrator.planner import (
    WorkerPlan,
    estimate_cost,
    plan_task,
)
from devin_orchestrator.signals import collect_signals

SCHEMA_NAMES = ("spec", "plan")


def _load_spec(spec_json: str) -> dict:
    try:
        return json.loads(spec_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid task spec JSON: {exc}") from exc


def _parse_files(values: list[str] | None) -> list[str]:
    """``--files`` is repeatable and comma-friendly: flatten both forms
    into one list of non-empty declared paths."""
    out: list[str] = []
    for chunk in values or []:
        for piece in chunk.split(","):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def _add_queue_parser(sub) -> None:
    """``queue`` — the persistent task queue (SQLite) the coordinator
    submits to and workers claim from. Local store only; it records and
    locks, it never spawns workers or runs task commands."""
    queue_p = sub.add_parser(
        "queue",
        help="persistent task queue (SQLite, local only): submit/claim/"
        "lease/collect units of work — records and locks, never spawns",
    )
    qsub = queue_p.add_subparsers(dest="queue_command", required=True)

    def db(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--queue", type=Path, metavar="DB",
            help="queue database path (default: "
            ".devin-ecosystem/task-queue.db — same family as plans.jsonl)",
        )

    def files_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--files", action="append", metavar="FILE[,FILE...]",
            help="declared paths the unit touches (repeatable, "
            "comma-separated); colliding with a live task's files "
            "refuses the submission",
        )

    s = qsub.add_parser(
        "submit", help="queue a unit of work: prints the task id as JSON")
    db(s)
    s.add_argument("payload", help="task payload as a JSON object")
    s.add_argument("--kind", default="task", metavar="KIND")
    s.add_argument("--repo", default="", metavar="REPO",
                   help="repo the unit targets (collision scope)")
    files_arg(s)
    s.add_argument("--batch", default="", metavar="NAME",
                   help="batch id grouping a fan-out for collect")
    s.add_argument("--key", metavar="IDEMPOTENCY-KEY",
                   help="resubmission-safe key: an existing key returns "
                   "the original task instead of duplicating work")
    s.add_argument("--max-attempts", type=int,
                   default=queue_db.DEFAULT_MAX_ATTEMPTS,
                   help="claim attempts before terminal failure "
                   f"(1..{queue_db.MAX_ATTEMPTS_CAP})")
    s.add_argument("--priority", type=int, default=0,
                   help="higher priority is claimed first (default 0)")

    f = qsub.add_parser(
        "fanout",
        help="submit a unit of work targeting an isolated git worktree: "
        "takes a physical worktree+branch lock and runs the file "
        "collision check; prints the task id as JSON")
    db(f)
    f.add_argument("repo", type=Path, help="local git repo the unit targets")
    f.add_argument("--branch", required=True, metavar="NAME",
                   help="branch the worktree will hold (locked per repo)")
    f.add_argument("--worktree", type=Path, metavar="PATH",
                   help="worktree path (default: "
                   "<repo-parent>/.devin-worktrees/<branch-slug>)")
    files_arg(f)
    f.add_argument("--batch", default="", metavar="NAME")
    f.add_argument("--key", metavar="IDEMPOTENCY-KEY")
    f.add_argument("--max-attempts", type=int,
                   default=queue_db.DEFAULT_MAX_ATTEMPTS)
    f.add_argument("--priority", type=int, default=0)
    f.add_argument("--payload", metavar="JSON", default=None,
                   help="extra task payload fields as a JSON object")

    c = qsub.add_parser(
        "claim", help="atomically claim the oldest queued task for a "
        "worker; prints the task JSON or 'null' when empty/at limit")
    db(c)
    c.add_argument("--worker", required=True, metavar="ID")
    c.add_argument("--kind", default=None, metavar="KIND",
                   help="only claim tasks of this kind")
    c.add_argument("--max-claims", type=int, default=1,
                   help="concurrent tasks this worker may hold "
                   f"(1..{queue_db.MAX_CLAIMS_PER_WORKER}, default 1)")
    c.add_argument("--lease", type=float,
                   default=queue_db.DEFAULT_LEASE_S, metavar="SECONDS",
                   help="lease length before the task is sweepable "
                   "(default %(default)s)")

    h = qsub.add_parser(
        "heartbeat", help="renew a held task's lease; the first "
        "heartbeat also marks it running")
    db(h)
    h.add_argument("task_id")
    h.add_argument("--worker", required=True, metavar="ID")
    h.add_argument("--lease", type=float,
                   default=queue_db.DEFAULT_LEASE_S, metavar="SECONDS")

    d = qsub.add_parser(
        "done", help="mark a held task succeeded (optionally with a "
        "JSON result) and release its worktree lock")
    db(d)
    d.add_argument("task_id")
    d.add_argument("--worker", required=True, metavar="ID")
    d.add_argument("--result", metavar="JSON", default=None,
                   help="result payload as a JSON object")

    x = qsub.add_parser(
        "fail", help="mark a held task failed — re-queues for a bounded "
        "retry while attempts remain; --final forces terminal")
    db(x)
    x.add_argument("task_id")
    x.add_argument("--worker", required=True, metavar="ID")
    x.add_argument("--error", required=True, metavar="MSG")
    x.add_argument("--final", action="store_true")

    k = qsub.add_parser(
        "cancel", help="cancel a non-terminal task (any actor may "
        "cancel; done/fail require the lease holder)")
    db(k)
    k.add_argument("task_id")
    k.add_argument("--reason", default="", metavar="MSG")

    w = qsub.add_parser(
        "sweep", help="orphan recovery: re-queue tasks whose lease "
        "expired without heartbeat (fail them when attempts are "
        "exhausted); prints counts as JSON")
    db(w)

    g = qsub.add_parser(
        "collect", help="gather finished task results before any "
        "aggregate report — stamps them collected; prints the collection "
        "doc as JSON")
    db(g)
    g.add_argument("--batch", default=None, metavar="NAME",
                   help="only collect this batch's tasks")
    g.add_argument("--all", action="store_true",
                   help="include tasks already collected")
    g.add_argument("--strict", action="store_true",
                   help="exit 1 when tasks in scope are still open")

    t = qsub.add_parser(
        "status", help="queue snapshot as JSON: counts per state, "
        "workers, worktree locks, expired leases")
    db(t)


def _cmd_queue(args: argparse.Namespace) -> int:
    db_path = args.queue or queue_db.default_queue_path()
    try:
        con = queue_db.connect(db_path)
    except (OSError, sqlite3.Error) as exc:
        print(f"error: cannot open queue {db_path}: {exc}", file=sys.stderr)
        return 2
    try:
        cmd = args.queue_command
        if cmd == "submit":
            try:
                payload = json.loads(args.payload)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid payload JSON: {exc}") from exc
            out = queue_db.submit(
                con, payload, kind=args.kind,
                files=_parse_files(args.files), repo=args.repo,
                batch=args.batch, idempotency_key=args.key,
                max_attempts=args.max_attempts, priority=args.priority)
        elif cmd == "fanout":
            extra = None
            if args.payload is not None:
                try:
                    extra = json.loads(args.payload)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid --payload JSON: {exc}") from exc
                if not isinstance(extra, dict):
                    raise ValueError("--payload must be a JSON object")
            out = queue_db.submit_fanout(
                con, str(args.repo), args.branch,
                files=_parse_files(args.files),
                worktree=str(args.worktree) if args.worktree else None,
                payload=extra, batch=args.batch,
                idempotency_key=args.key,
                max_attempts=args.max_attempts, priority=args.priority)
        elif cmd == "claim":
            out = queue_db.claim(
                con, args.worker, kind=args.kind,
                max_claims=args.max_claims, lease_s=args.lease)
        elif cmd == "heartbeat":
            out = queue_db.heartbeat(
                con, args.task_id, args.worker, lease_s=args.lease)
        elif cmd == "done":
            result = None
            if args.result is not None:
                try:
                    result = json.loads(args.result)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid --result JSON: {exc}") from exc
                if not isinstance(result, dict):
                    raise ValueError("--result must be a JSON object")
            out = queue_db.done(con, args.task_id, args.worker, result)
        elif cmd == "fail":
            out = queue_db.fail(
                con, args.task_id, args.worker, args.error,
                final=args.final)
        elif cmd == "cancel":
            out = queue_db.cancel(con, args.task_id, reason=args.reason)
        elif cmd == "sweep":
            out = queue_db.sweep(con)
        elif cmd == "collect":
            out = queue_db.collect(
                con, batch=args.batch, include_collected=args.all)
        elif cmd == "status":
            out = queue_db.status(con)
        else:  # pragma: no cover - argparse enforces the subcommand set
            raise ValueError(f"unknown queue command {cmd!r}")
    except (ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        con.close()
        return 2
    con.close()
    print(json.dumps(out, indent=2))
    if cmd == "collect" and args.strict and not out["complete"]:
        return 1
    return 0


def _explain(spec: dict, plan: WorkerPlan, cost: bool = False) -> str:
    """Human-readable walkthrough of the plan decision (OR-2)."""
    lines = [f"Plan: {plan.workers} worker(s), mode={plan.mode}"]
    if plan.nesting_blocked:
        lines.append("- this session is already inside a worker; nesting is forbidden")
    else:
        lines.append(f"- {plan.rationale}")
    if plan.profiles:
        lines.append(f"- profile(s): {', '.join(sorted(set(plan.profiles)))}")
    if plan.units:
        lines.append(
            "- trigger DAG order: "
            + " -> ".join(u["id"] for u in plan.units))
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
    group = plan_p.add_mutually_exclusive_group()
    group.add_argument("spec", nargs="?", help="task spec as a JSON string")
    group.add_argument("--spec-file", type=Path, help="path to a JSON task spec")
    group.add_argument(
        "--from-triggers", type=Path, metavar="DIR",
        help="read PAS trigger manifests (*.{yaml,yml,json}) from DIR, "
        "resolve their after/requires.artifacts DAG, and plan fan-out "
        "over the DAG-ordered units",
    )
    plan_p.add_argument(
        "--event", metavar="EVENT",
        help="with --from-triggers: only manifests bound to this "
        "trigger.event become units",
    )
    plan_p.add_argument(
        "--explain", action="store_true",
        help="print a human-readable decision walkthrough instead of JSON",
    )
    plan_p.add_argument(
        "--cost-estimate", action="store_true",
        help="add a per-unit duration estimate (minutes; a rough planning "
        "label — sequential vs fan-out critical path, not billing)",
    )
    plan_p.add_argument(
        "--files", action="append", metavar="FILE[,FILE...]",
        help="declare the work units directly: one unit per file "
        "(repeatable; comma-separated accepted). Builds units_detail, "
        "sets independent_units to the file count, and runs the same "
        "collision check as units_detail. Usable alone — the spec "
        "then defaults to an empty object",
    )
    plan_p.add_argument(
        "--emit-manifest", action="store_true",
        help="serialize the plan as a PAS trigger manifest "
        "(devin-catalog §8 format) instead of plan JSON — the spec "
        "becomes the action's single job description",
    )
    plan_p.add_argument(
        "--manifest-id", metavar="ID",
        help="manifest id (default: orchestrator.plan-<spec hash>); "
        "must match <owner>.<slug>",
    )
    plan_p.add_argument(
        "--manifest-event", metavar="EVENT",
        help="trigger.event for the emitted manifest (default: "
        "Scheduled); non-Scheduled events carry no schedule",
    )
    plan_p.add_argument(
        "--schedule", metavar="EXPR",
        help="with event=Scheduled: 'every <N>m|h' | 'hourly' | "
        "'daily [HH:MM]' | 'weekly <dow> [HH:MM]' (default: daily)",
    )
    plan_p.add_argument(
        "--severity", choices=("p0", "p1", "p2"), default=None,
        help="manifest severity (default: p2)",
    )
    plan_p.add_argument(
        "--format", choices=("yaml", "json"), default=None,
        help="manifest serialization format (default: yaml)",
    )
    plan_p.add_argument(
        "--out", type=Path, metavar="FILE",
        help="with --emit-manifest: write the manifest to FILE instead "
        "of stdout",
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
    sub.add_parser(
        "signals",
        help="print the machine signals the planner honours "
        "(ram_pressure, inside_worker) as JSON",
    )
    _add_queue_parser(sub)
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

    if args.command == "signals":
        print(json.dumps(collect_signals(), indent=2))
        return 0

    if args.command == "queue":
        return _cmd_queue(args)

    if args.files and args.from_triggers is not None:
        print("error: --files does not apply to --from-triggers",
              file=sys.stderr)
        return 2

    if (args.spec is None and args.spec_file is None
            and args.from_triggers is None and not args.files):
        print("error: a task spec is required — pass spec, --spec-file, "
              "--from-triggers or --files", file=sys.stderr)
        return 2

    if args.event and args.from_triggers is None:
        print("error: --event only applies to --from-triggers",
              file=sys.stderr)
        return 2

    emit_only_flags = (
        args.manifest_id or args.manifest_event or args.schedule
        or args.severity or args.format or args.out)
    if emit_only_flags and not args.emit_manifest:
        print("error: --manifest-id/--manifest-event/--schedule/"
              "--severity/--format/--out only apply with --emit-manifest",
              file=sys.stderr)
        return 2
    if args.emit_manifest and (args.explain or args.cost_estimate):
        print("error: --emit-manifest produces only the manifest; "
              "--explain/--cost-estimate do not apply", file=sys.stderr)
        return 2
    if args.emit_manifest and args.from_triggers is not None:
        print("error: --emit-manifest does not apply to --from-triggers",
              file=sys.stderr)
        return 2

    try:
        if args.from_triggers is not None:
            spec, units, dag_warnings = trigger_manifests.spec_from_triggers(
                args.from_triggers, args.event)
            plan = plan_task(spec)
            plan.units = units
            plan.warnings = dag_warnings + plan.warnings
            if not units:
                plan.warnings.insert(
                    0, "no enabled trigger manifests — 0 work units")
        else:
            if args.spec is not None:
                raw = args.spec
            elif args.spec_file is not None:
                raw = args.spec_file.read_text(encoding="utf-8")
            else:
                raw = "{}"  # --files alone: a default spec is enough
            spec = _load_spec(raw)
            declared = _parse_files(args.files)
            if declared:
                if spec.get("units_detail"):
                    raise ValueError(
                        "--files and spec 'units_detail' are mutually "
                        "exclusive — declare units one way or the other")
                spec["units_detail"] = [
                    {"id": f"unit-{i}", "files": [f]}
                    for i, f in enumerate(declared, 1)
                ]
                requested = spec.get("independent_units")
                spec["independent_units"] = len(declared)
                plan = plan_task(spec)
                plan.units = [
                    {"id": u["id"], "files": u["files"],
                     "event": None, "after": []}
                    for u in spec["units_detail"]
                ]
                if requested not in (None, len(declared)):
                    plan.warnings.insert(
                        0,
                        f"independent_units={requested} overridden by "
                        f"--files ({len(declared)} unit(s))")
            else:
                plan = plan_task(spec)
        if args.emit_manifest:
            event = args.manifest_event or plan_emit.DEFAULT_EVENT
            schedule = args.schedule
            if event == plan_emit.DEFAULT_EVENT and schedule is None:
                schedule = plan_emit.DEFAULT_SCHEDULE
            manifest = plan_emit.plan_manifest(
                spec, plan,
                manifest_id=args.manifest_id,
                event=event,
                schedule=schedule,
                severity=args.severity or "p2")
            text = (
                plan_emit.manifest_to_json(manifest)
                if args.format == "json"
                else plan_emit.manifest_to_yaml(manifest, plan))
            if args.out:
                args.out.write_text(text, encoding="utf-8")
            else:
                print(text, end="" if text.endswith("\n") else "\n")
            return 0
        if args.explain:
            out = _explain(spec, plan, cost=args.cost_estimate)
        elif args.cost_estimate:
            doc = plan.to_dict()
            doc["cost_estimate"] = estimate_cost(spec, plan)
            out = json.dumps(doc, indent=2)
        else:
            out = json.dumps(plan.to_dict(), indent=2)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
