# STATUS — devin-orchestrator

## Done (M1)

- Deterministic planner (`src/devin_orchestrator/planner.py`): worker caps,
  nesting ban, profile selection, collect-before-report contract.
- CLI: `devin-orchestrator plan <spec-json>`.
- Skill (`.devin/skills/devin-orchestrator/`) + always-on rule
  (`.devin/rules/background-workers.md`).
- 12+ tests covering the required matrix; CI matrix (Windows + Ubuntu).
- `record` emits a per-run `metrics.json` (`metrics/<run_id>.json` beside
  `plans.jsonl`): ids, timestamps, per-unit status/duration, outcome
  counts. Always written — read-only observability.
- `plan --cost-estimate`: per-unit duration estimate + sequential vs.
  fan-out critical path (minutes; planning label, not billing).
- `queue`: persistent SQLite task queue
  (`.devin-ecosystem/task-queue.db`) — explicit states (queued → claimed
  → running → succeeded|failed|cancelled), atomic BEGIN IMMEDIATE
  claims bounded per worker, heartbeat-renewed leases with `sweep`
  orphan recovery, bounded retries + idempotency keys, `collect`
  (stamps terminal results, reports stragglers before the aggregate
  report), and secret-screened writes. Records and locks only — never
  spawns workers.
- `queue fanout`: worktree-targeted submissions with a physical
  (repo, worktree)+(repo, branch) lock and `file_collisions`-style
  refusal against live tasks.
- `plan --files`: declare units directly as a file list (one
  constrained unit per file; mutually exclusive with `units_detail` and
  `--from-triggers`).

## M2 backlog

- [ ] Wire planner output into session bootstrap so plans are logged to
      `.devin/memory/orchestrator-plans.jsonl` for auditing.
- [ ] Integrate with `devin-repo-task` dispatcher (parallel repo batches
      reuse the same cap).
- [ ] Publish to PyPI.
