---
name: devin-orchestrator
description: "Consult the local plan registry of devin-orchestrator — what plans were recorded and their outcomes. Read-only: this skill never starts, plans, or dispatches fan-out; orchestration decisions and worker dispatch are a human action via the CLI."
triggers: [model, user]
---

# Devin Orchestrator — plan registry, read-only

This skill's only surface is **consulting** the local plan registry:
which orchestration plans exist on record and what their recorded
outcomes were.

```bash
devin-orchestrator history            # count + per-outcome summary, human-readable
devin-orchestrator history --json     # summary plus every individual record
```

`--registry <path>` reads a non-default `plans.jsonl` location.

## What this skill is not

- **It never starts fan-out.** Emitting a plan
  (`devin-orchestrator plan <spec>`) is the instruction a dispatch would
  follow — creating one counts as initiating orchestration, so it stays
  a deliberate human CLI action, outside this surface.
- **It never records outcomes.** `devin-orchestrator record` appends to
  `plans.jsonl`; the registry is written by the human or the
  orchestration run itself, not by the agent through this skill.
- **It does not dispatch or kill workers.** There is no spawn/stop
  command anywhere in this surface — by design, not omission.

If the user asks to plan or run a fan-out, surface the CLI commands for
*them* to run — do not run `plan` or `record` yourself from this skill.
