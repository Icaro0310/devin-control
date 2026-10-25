"""Serialize a WorkerPlan as a PAS trigger manifest.

``devin-catalog`` (spec §8) consumes declarative trigger manifests —
``trigger.event: Scheduled`` entries get installed via Task Scheduler /
cron and re-fire through the session-dispatcher. ``--emit-manifest``
turns a plan into one of those manifests so a fan-out can be scheduled.

The manifest schema requires an ``action.command``. Since a plan must
never carry unit-level shell, the emitted action is the orchestrator's
own read-only ``plan`` call with the canonical spec as its single
argument: firing it re-derives the plan under the machine signals live
at that moment (``ram_pressure`` may cap it further), which is exactly
the behavior a scheduled fan-out wants. The spec JSON *is* the job
description — one command, no shell fragments built from unit data.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from devin_orchestrator.planner import WorkerPlan

MANIFEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z0-9-]+$")
SEVERITIES = ("p0", "p1", "p2")
SCOPES = ("session", "repository", "workspace", "global", "artifact")

# Same grammar devin-catalog enforces for trigger.schedule.
SCHEDULE_RE = re.compile(
    r"^(every\s+\d+[mh]"
    r"|hourly"
    r"|daily(\s+[0-2]?\d:[0-5]\d)?"
    r"|weekly\s+(mon|tue|wed|thu|fri|sat|sun)(\s+[0-2]?\d:[0-5]\d)?)$",
    re.IGNORECASE,
)

DEFAULT_EVENT = "Scheduled"
DEFAULT_SCHEDULE = "daily"
DEFAULT_SCOPE = "workspace"
ACTION_TIMEOUT_S = 120


# Strings that would round-trip as another YAML type must stay quoted.
_YAML_KEYWORDS = {
    "true", "false", "null", "yes", "no", "on", "off", "~", ""}
_NUMBERISH_RE = re.compile(r"[+-]?[\d.]+")


def _yaml_scalar(value: Any) -> str:
    """Render a scalar for the emitted manifest: bare when unambiguous,
    single-quoted otherwise (``'`` escaped as ``''``)."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("manifest values must be single-line")
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", text)
        and text.lower() not in _YAML_KEYWORDS
        and not _NUMBERISH_RE.fullmatch(text)
    ):
        return text
    return "'" + text.replace("'", "''") + "'"


def plan_manifest(
    spec: dict[str, Any],
    plan: WorkerPlan,
    *,
    manifest_id: str | None = None,
    event: str = DEFAULT_EVENT,
    schedule: str | None = DEFAULT_SCHEDULE,
    scope: str = DEFAULT_SCOPE,
    severity: str = "p2",
) -> dict[str, Any]:
    """Build the trigger-manifest dict for a spec under ``plan``.

    Raises ``ValueError`` on an invalid id/schedule/event/severity —
    the emitted manifest must validate against
    ``.devin/catalog/trigger.schema.json`` unchanged.
    """
    mid = manifest_id
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    if mid is None:
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:8]
        mid = f"orchestrator.plan-{digest}"
    if not MANIFEST_ID_RE.match(mid):
        raise ValueError(
            f"manifest id {mid!r} must match {MANIFEST_ID_RE.pattern} "
            "(<owner>.<slug>)")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    if not isinstance(event, str) or not event.strip():
        raise ValueError("trigger event must be a non-empty string")
    trigger: dict[str, Any] = {"event": event, "scope": scope}
    if event == DEFAULT_EVENT:
        if not schedule or not SCHEDULE_RE.match(schedule.strip()):
            raise ValueError(
                "event=Scheduled requires --schedule matching "
                "'every <N>m|h' | 'hourly' | 'daily [HH:MM]' | "
                "'weekly <dow> [HH:MM]'")
        trigger["schedule"] = schedule.strip()
    elif schedule is not None:
        raise ValueError(
            f"--schedule only applies to event={DEFAULT_EVENT}; "
            f"event {event!r} fires from the dispatcher")

    return {
        "id": mid,
        "version": 1,
        "owner": "devin-orchestrator",
        "enabled": True,
        "severity": severity,
        "class": "enricher",
        "trigger": trigger,
        "action": {
            # The single job description: re-derive this exact spec's
            # plan at fire time. Read-only, no side effects.
            "command": f"devin-orchestrator plan '{canonical}'",
            "mode": "sync",
            "timeout": ACTION_TIMEOUT_S,
        },
        # Constant Scheduled input must not dedup — the catalog requires
        # idempotency.disabled on clock-fired handlers.
        "idempotency": {"disabled": True},
        "on_hook_failure": "fail-open",
        "artifacts": {"output": "worker-plan"},
    }


def manifest_to_yaml(manifest: dict[str, Any],
                     plan: WorkerPlan | None = None) -> str:
    """Render the manifest dict as deterministic YAML (subset-safe)."""
    lines: list[str] = [
        "# Generated by devin-orchestrator — WorkerPlan as a PAS trigger",
        "# manifest (devin-skill-catalog §8). Install with",
        "# `devin-catalog.py schedule plan|install` in the workspace.",
    ]
    if plan is not None:
        lines.append(
            f"# plan at emit time: workers={plan.workers} "
            f"mode={plan.mode} collect={str(plan.collect).lower()} "
            f"— re-derived live at fire time")
    lines += [
        f"id: {_yaml_scalar(manifest['id'])}",
        f"version: {manifest['version']}",
        f"owner: {_yaml_scalar(manifest['owner'])}",
        f"enabled: {_yaml_scalar(manifest['enabled'])}",
        f"severity: {_yaml_scalar(manifest['severity'])}",
        f"class: {_yaml_scalar(manifest['class'])}",
        "",
        "trigger:",
        f"  event: {_yaml_scalar(manifest['trigger']['event'])}",
        f"  scope: {_yaml_scalar(manifest['trigger']['scope'])}",
    ]
    if manifest["trigger"].get("schedule"):
        lines.append(
            f"  schedule: {_yaml_scalar(manifest['trigger']['schedule'])}")
    lines += [
        "",
        "action:",
        f"  command: {_yaml_scalar(manifest['action']['command'])}",
        f"  mode: {manifest['action']['mode']}",
        f"  timeout: {manifest['action']['timeout']}",
        "",
        "idempotency:",
        "  disabled: true",
        "on_hook_failure: fail-open",
        "",
        "artifacts:",
        f"  output: {_yaml_scalar(manifest['artifacts']['output'])}",
    ]
    return "\n".join(lines) + "\n"


def manifest_to_json(manifest: dict[str, Any]) -> str:
    """JSON serialization — the catalog loader also accepts ``*.json``."""
    return json.dumps(manifest, indent=2) + "\n"
