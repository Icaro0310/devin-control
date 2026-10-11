"""OR-3: local plan registry — plan vs. outcome, on disk only.

Not telemetry: nothing leaves the machine. ``record`` appends a plan hash
plus its outcome to a JSONL log; ``history`` reads it back so future plans
can be compared against what actually happened. The registry lives under
the ecosystem config dir by default and is human-readable throughout.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OUTCOMES = ("success", "partial", "failed", "aborted")

METRICS_VERSION = 1


def default_registry_path() -> Path:
    """``<config>/.devin-ecosystem/plans.jsonl`` — same root the hook
    dispatcher and scheduler already share."""
    override = os.environ.get("DEVIN_ECOSYSTEM_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "plans.jsonl"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "devin-ecosystem" / "plans.jsonl"
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / ".devin-ecosystem" / "plans.jsonl"


def _plan_hash(plan: dict[str, Any]) -> str:
    blob = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _safe_name(run_id: str) -> str:
    """Run ids become filenames under ``metrics/`` — slugs only, so an
    adversarial id can never escape the metrics directory."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id)
    safe = safe.replace("..", "-").strip("-.")
    return safe or "run"


def metrics_dir(registry: Path) -> Path:
    """Per-run ``metrics.json`` files live in ``metrics/`` beside
    ``plans.jsonl`` — the same ``.devin-ecosystem`` directory family."""
    return registry.parent / "metrics"


def metrics_path(registry: Path, run_id: str) -> Path:
    return metrics_dir(registry) / f"{_safe_name(run_id)}.json"


@dataclass
class PlanRecord:
    ts: float
    plan_hash: str
    workers: int
    mode: str | None
    outcome: str
    notes: str
    run_id: str = ""
    metrics_path: str | None = None
    metrics_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "plan_hash": self.plan_hash,
            "workers": self.workers,
            "mode": self.mode,
            "outcome": self.outcome,
            "notes": self.notes,
            "run_id": self.run_id,
        }


def _normalise_units(raw: Any) -> list[dict[str, Any]]:
    """Validate the caller-supplied per-unit result list."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("run units must be a JSON array")  # noqa: TRY004 - callers/tests catch ValueError
    out: list[dict[str, Any]] = []
    for u in raw:
        if (
            not isinstance(u, dict)
            or not isinstance(u.get("id"), str)
            or not u["id"]
        ):
            raise ValueError(
                "run units entries must be objects with a non-empty 'id'")
        status = u.get("status", "unknown")
        if not isinstance(status, str) or not status:
            raise ValueError("run units[].status must be a non-empty string")
        dur = u.get("duration_s")
        if dur is not None and (
            not isinstance(dur, (int, float))
            or isinstance(dur, bool)
            or not math.isfinite(dur)
            or dur < 0
        ):
            raise ValueError(
                "run units[].duration_s must be a non-negative number")
        out.append({"id": u["id"], "status": status, "duration_s": dur})
    return out


def build_metrics(rec: PlanRecord, run: dict[str, Any]) -> dict[str, Any]:
    """Shape of the per-run ``metrics.json`` file (``version`` 1).

    ``ended_ts`` is the record timestamp — the run is finished by the time
    its outcome is recorded. ``started_ts``/``duration_s`` are supplied by
    the caller when known; each is derived from the other when only one is
    given. ``outcome_counts`` rolls up per-unit statuses, falling back to
    the run's own outcome when no unit detail was supplied.
    """
    units = _normalise_units(run.get("units"))
    duration = run.get("duration_s")
    if duration is not None and (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise ValueError("run duration_s must be a non-negative number")
    started = run.get("started")
    if started is not None and (
        not isinstance(started, (int, float))
        or isinstance(started, bool)
        or not math.isfinite(started)
    ):
        raise ValueError("run started must be a Unix timestamp (number)")
    ended = rec.ts
    if duration is None and started is not None:
        duration = round(ended - started, 3)
        if duration < 0:
            raise ValueError("run started is after the record timestamp")
    if started is None and duration is not None:
        started = round(ended - duration, 3)
    counts: dict[str, int] = {}
    for u in units:
        counts[u["status"]] = counts.get(u["status"], 0) + 1
    if not counts:
        counts[rec.outcome] = 1
    return {
        "version": METRICS_VERSION,
        "run_id": rec.run_id,
        "session_id": run.get("session_id"),
        "plan_hash": rec.plan_hash,
        "recorded_ts": rec.ts,
        "started_ts": started,
        "ended_ts": ended,
        "duration_s": duration,
        "workers": rec.workers,
        "mode": rec.mode,
        "outcome": rec.outcome,
        "units": units,
        "outcome_counts": counts,
        "note": "local observability only — nothing leaves the machine",
    }


def _write_metrics_doc(registry: Path, run_id: str,
                       doc: dict[str, Any]) -> Path:
    path = metrics_path(registry, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def record(registry: Path, plan: dict[str, Any], outcome: str,
           notes: str = "", run: dict[str, Any] | None = None) -> PlanRecord:
    """Append one outcome to the JSONL registry (atomic-ish: single write)
    and emit the run's ``metrics.json`` alongside it.

    The metrics file is observability only and is always written — there is
    no opt-out. ``run`` carries whatever the caller knows about the actual
    execution: ``run_id``, ``session_id``, ``started`` (Unix ts),
    ``duration_s`` and ``units`` (``[{"id","status","duration_s"}]``).
    Validation errors abort the record before anything is written; a
    metrics *write* failure never breaks the record itself — it lands on
    ``rec.metrics_error`` instead.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    run = dict(run or {})
    ts = time.time()
    plan_hash = _plan_hash(plan)
    run_id = _safe_name(
        str(run.get("run_id") or f"run-{int(ts * 1000)}-{plan_hash[:8]}"))
    # run ids double as metrics filenames — distinct raw ids can slug to
    # the same name ("a/b" vs "a-b") and an id may legitimately be
    # recorded twice. Uniquify the recorded id so its metrics file is
    # never clobbered and metrics_path() resolves every run to its own
    # document.
    base = run_id
    i = 2
    while metrics_path(registry, run_id).exists():
        run_id = f"{base}-{i}"
        i += 1
    rec = PlanRecord(
        ts=ts,
        plan_hash=plan_hash,
        workers=len(plan.get("workers", []))
        if isinstance(plan.get("workers"), list)
        else int(plan.get("workers", 0) or 0),
        mode=plan.get("mode"),
        outcome=outcome,
        notes=notes,
        run_id=run_id,
    )
    doc = build_metrics(rec, run)  # raises ValueError before any write
    registry.parent.mkdir(parents=True, exist_ok=True)
    with open(registry, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec.to_dict(), separators=(",", ":")) + "\n")
    try:
        rec.metrics_path = str(_write_metrics_doc(registry, run_id, doc))
    except OSError as exc:  # observability must never break recording
        rec.metrics_error = f"metrics.json not written: {exc}"
    return rec


def iter_records(registry: Path) -> Iterator[dict[str, Any]]:
    if not registry.is_file():
        return
    for line in registry.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def summarize(registry: Path) -> dict[str, Any]:
    recs = list(iter_records(registry))
    by_outcome: dict[str, int] = {}
    for r in recs:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
    return {
        "registry": str(registry),
        "total": len(recs),
        "by_outcome": by_outcome,
        "note": "local registry only — nothing leaves the machine",
    }
