"""Deterministic fan-out planner.

Input: a task spec (dict) with structured signals supplied by the calling
agent. Output: a WorkerPlan describing how many background subagents to
spawn, with which profile, and the contract the parent must honour.

The planner NEVER decides *what* to decompose — the model does that. It
decides *whether* fan-out is warranted and enforces hard limits:

- trivial / question / explanation tasks -> 0 workers
- N independent units -> min(N, MAX_WORKERS) workers
- large refactor / multi-area change -> up to MAX_WORKERS workers
- nested fan-out (called from inside a worker) -> 0 workers + warning
- every plan with workers carries collect=True: the parent MUST gather
  results via read_subagent / completion notifications before reporting.
- machine signals (``signals.collect_signals``) bound the plan too:
  ``ram_pressure`` caps fan-out at one worker. An unreadable signal is
  absent evidence, never clearance.

The plan is data only. It contains no file paths, no repo URLs and no
commands — the planner cannot create repositories or perform I/O.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any

from devin_orchestrator.signals import collect_signals

MAX_WORKERS = 3
DEFAULT_READ_PROFILE = "subagent_explore"
DEFAULT_WRITE_PROFILE = "subagent_general"

VALID_KINDS = {"implementation", "question", "review", "fix", "refactor", "research"}
VALID_SCOPES = {"trivial", "small", "medium", "large"}

# Fallback unit durations for --cost-estimate when the spec declares none.
SCOPE_UNIT_MINUTES = {"trivial": 5, "small": 15, "medium": 30, "large": 60}


def _check_minutes(value: Any, field: str) -> float | None:
    """Optional positive-number validator for estimated-minute fields."""
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field} must be a positive number")
    return float(value)


@dataclass
class WorkerPlan:
    """Fan-out decision for one task."""

    workers: int
    mode: str  # "none" | "background"
    profiles: list[str] = field(default_factory=list)
    rationale: str = ""
    collect: bool = False  # parent must gather results before reporting
    nesting_blocked: bool = False
    warnings: list[str] = field(default_factory=list)
    file_collisions: list[dict[str, Any]] = field(default_factory=list)
    signals: dict[str, Any] | None = None  # machine signals in force
    units: list[dict[str, Any]] | None = None  # DAG-ordered trigger units

    def to_dict(self) -> dict[str, Any]:
        out = {
            "workers": self.workers,
            "mode": self.mode,
            "profiles": self.profiles,
            "rationale": self.rationale,
            "collect": self.collect,
            "nesting_blocked": self.nesting_blocked,
            "warnings": self.warnings,
            "limits": {"max_workers": MAX_WORKERS, "nesting": "forbidden"},
        }
        if self.file_collisions:
            out["file_collisions"] = self.file_collisions
        if self.signals is not None:
            out["signals"] = self.signals
        if self.units is not None:
            out["units"] = self.units
        return out


def _max_workers(env: dict[str, str] | None = None) -> int:
    """Hard cap; an env var can only lower it, never raise past MAX_WORKERS."""
    env = env if env is not None else os.environ
    try:
        requested = int(env.get("DEVIN_MAX_WORKERS", ""))
    except ValueError:
        return MAX_WORKERS
    return max(1, min(requested, MAX_WORKERS))


def _inside_worker(env: dict[str, str] | None = None) -> bool:
    env = env if env is not None else os.environ
    return env.get("DEVIN_INSIDE_SUBAGENT", "") == "1"


def _normalise_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and coerce the input spec; raises ValueError on bad input."""
    if not isinstance(spec, dict):
        raise ValueError("task spec must be a JSON object")  # noqa: TRY004 - callers/tests catch ValueError
    kind = spec.get("kind", "implementation")
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}")
    scope = spec.get("estimated_scope", "small")
    if scope not in VALID_SCOPES:
        raise ValueError(f"estimated_scope must be one of {sorted(VALID_SCOPES)}")
    units = spec.get("independent_units", 1)
    if not isinstance(units, int) or isinstance(units, bool) or units < 0:
        raise ValueError("independent_units must be a non-negative integer")
    detail = spec.get("units_detail")
    if detail is not None:
        if not isinstance(detail, list) or not all(
            isinstance(u, dict) and isinstance(u.get("id"), str) and u["id"]
            for u in detail
        ):
            raise ValueError(
                "units_detail must be a list of objects with a non-empty 'id'")
        for u in detail:
            files = u.get("files", [])
            if not isinstance(files, list) or not all(
                isinstance(f, str) and f for f in files
            ):
                raise ValueError(
                    "units_detail[].files must be a list of non-empty strings")
            _check_minutes(u.get("est_minutes"), "units_detail[].est_minutes")
    return {
        "kind": kind,
        "estimated_scope": scope,
        "independent_units": units,
        "needs_write": bool(spec.get("needs_write", True)),
        "summary": str(spec.get("summary", ""))[:200],
        "units_detail": detail,
        "est_unit_minutes": _check_minutes(
            spec.get("est_unit_minutes"), "est_unit_minutes"),
    }


def _norm_path(p: str) -> str:
    """Declared paths normalize to forward slashes; never resolved on disk."""
    return p.replace("\\", "/").strip("/")


def _paths_overlap(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def file_collisions(units_detail: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Static disjointness check over declared unit file lists (OR-4).

    Pure declaration checking — the planner never touches the filesystem.
    Two paths collide when equal or when one is a directory prefix of the
    other. ``files`` absent on a unit means the unit declares no paths.
    """
    if not units_detail:
        return []
    out: list[dict[str, Any]] = []
    for i in range(len(units_detail)):
        for j in range(i + 1, len(units_detail)):
            ui, uj = units_detail[i], units_detail[j]
            for fa in ui.get("files", []):
                for fb in uj.get("files", []):
                    na, nb = _norm_path(fa), _norm_path(fb)
                    if _paths_overlap(na, nb):
                        out.append({
                            "units": [ui["id"], uj["id"]],
                            "path": na if len(na) <= len(nb) else nb,
                        })
    return out


def plan_task(
    spec: dict[str, Any],
    env: dict[str, str] | None = None,
    signals: dict[str, Any] | None = None,
) -> WorkerPlan:
    """Decide how many background workers a task should fan out to.

    spec keys:
      kind               one of VALID_KINDS (default "implementation")
      estimated_scope    trivial|small|medium|large (default "small")
      independent_units  int — count of genuinely disjoint work units
      needs_write        bool — workers need write access (default True)
      summary            str — short task label for the rationale

    env: injectable environment (tests); defaults to os.environ.
    signals: machine signals from ``signals.collect_signals``; None
        collects them live. ``ram_pressure`` caps fan-out at 1 worker —
        a constrained host is a reason to stay small, never a reason to
        fan out harder.
    """
    task = _normalise_spec(spec)
    if signals is None:
        signals = collect_signals(env)
    cap = _max_workers(env)
    pressure = signals.get("ram_pressure") is True
    if pressure:
        cap = min(cap, 1)

    if _inside_worker(env):
        return WorkerPlan(
            workers=0,
            mode="none",
            rationale="nested fan-out is forbidden — this is already a worker",
            nesting_blocked=True,
            warnings=["DEVIN_INSIDE_SUBAGENT=1 detected; refusing to nest"],
        )

    kind = task["kind"]
    scope = task["estimated_scope"]
    units = task["independent_units"]

    # 1. Nothing to parallelise.
    if kind in {"question"} or scope == "trivial":
        return WorkerPlan(
            workers=0,
            mode="none",
            rationale=f"{kind}/{scope} tasks run inline — fan-out overhead is not worth it",
            signals=signals,
        )

    # 2. Review/research of a single area, or single-unit work: stay inline
    #    unless the scope is large enough to split.
    if units <= 1 and scope in {"small", "medium"}:
        return WorkerPlan(
            workers=0,
            mode="none",
            rationale="single work unit — inline execution keeps the session simple",
            signals=signals,
        )

    # 3. Fan out: one worker per independent unit, capped.
    if units >= 2:
        count = min(units, cap)
        rationale = (
            f"{units} independent units -> {count} background worker(s)"
            + (" (capped)" if units > cap else "")
        )
    else:
        # Large single-area task (refactor/migration): bounded decomposition.
        count = cap if scope == "large" else 1
        rationale = f"large {kind} — up to {count} worker(s) for disjoint slices"

    profile = DEFAULT_WRITE_PROFILE if task["needs_write"] else DEFAULT_READ_PROFILE
    warnings = []
    if kind in {"review", "research"} and task["needs_write"]:
        warnings.append("review/research tasks should set needs_write=false")
        profile = DEFAULT_READ_PROFILE

    collisions = file_collisions(task["units_detail"])
    if collisions:
        pair_ids = {
            tuple(sorted(c["units"])) for c in collisions
        }
        warnings.append(
            f"{len(collisions)} file collision(s) across "
            f"{len(pair_ids)} unit pair(s) — units are NOT disjoint; "
            "see file_collisions"
        )
    if pressure:
        avail = signals.get("ram_available_mb")
        warnings.append(
            "ram_pressure signal active — host RAM constrained "
            f"({avail} MB available); fan-out capped at 1 worker"
        )

    return WorkerPlan(
        workers=count,
        mode="background",
        profiles=[profile] * count,
        rationale=rationale,
        collect=True,
        warnings=warnings,
        file_collisions=collisions,
        signals=signals,
    )


def estimate_cost(spec: dict[str, Any], plan: WorkerPlan) -> dict[str, Any]:
    """Rough per-unit duration estimate for a spec under a given plan.

    Planning label only — unit is *minutes*, never currency, quota or
    billing. Durations come from ``units_detail[].est_minutes``, then
    ``est_unit_minutes``, then a per-scope default. The fan-out figure is a
    greedy longest-first schedule across ``plan.workers`` lanes; its
    critical path is the longest lane. With 0 workers everything runs
    sequentially in the main session and the two numbers coincide.
    """
    task = _normalise_spec(spec)
    detail = task["units_detail"] or []
    n = max(task["independent_units"], len(detail))
    default_min = (
        task["est_unit_minutes"]
        or SCOPE_UNIT_MINUTES[task["estimated_scope"]]
    )
    units: list[dict[str, Any]] = []
    for i in range(n):
        d = detail[i] if i < len(detail) else {}
        mins = _check_minutes(d.get("est_minutes"), "units_detail[].est_minutes")
        units.append({
            "id": d.get("id", f"unit-{i + 1}"),
            "est_minutes": float(mins or default_min),
        })
    seq_total = sum(u["est_minutes"] for u in units)
    sequential = round(seq_total, 2)
    w = plan.workers
    if w <= 0 or n == 0:
        fan_raw = seq_total
    elif n == 1:
        # A large single-area task is decomposed into disjoint slices —
        # model the critical path as an even split across the workers.
        fan_raw = seq_total / w
    else:
        lanes = [0.0] * w
        for m in sorted((u["est_minutes"] for u in units), reverse=True):
            lanes[lanes.index(min(lanes))] += m
        fan_raw = max(lanes)
    fanout = round(fan_raw, 2)
    if w <= 0:
        sched = "0 workers planned — everything runs sequentially inline"
    elif n == 1:
        sched = (
            f"single unit decomposed evenly across {w} worker lane(s); "
            "critical path = unit / workers"
        )
    else:
        sched = (
            "fan-out = greedy longest-first scheduling across "
            f"{w} worker lane(s); critical path = longest lane"
        )
    assumptions = [
        (
            "unit durations: units_detail[].est_minutes, else spec "
            "est_unit_minutes, else scope default "
            f"({task['estimated_scope']}="
            f"{SCOPE_UNIT_MINUTES[task['estimated_scope']]} min)"
        ),
        sched,
        "excludes worker spawn, result collection and verification overhead",
    ]
    return {
        "kind": "duration_estimate",
        "unit": "minutes",
        "unit_count": n,
        "units": units,
        "planned_workers": w,
        "estimated_sessions": 1 + w,  # one coordinator + one per worker
        "sequential_minutes": sequential,
        "fanout_minutes": fanout,
        # speedup is computed on the unrounded totals — display rounding
        # alone must not collapse a real saving to 1.0
        "speedup": round(seq_total / fan_raw, 2) if fan_raw else 1.0,
        "assumptions": assumptions,
        "note": "rough planning estimate in minutes — not billing or quota data",
    }


def plan_task_json(spec_json: str, env: dict[str, str] | None = None) -> str:
    """CLI helper: parse a spec JSON string and return the plan JSON."""
    try:
        spec = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid task spec JSON: {exc}") from exc
    return json.dumps(plan_task(spec, env).to_dict(), indent=2)
