# Changelog

## [Unreleased]

### Added

- `queue` — a persistent task queue in a local SQLite file
  (`.devin-ecosystem/task-queue.db`, `--queue` overrides; same directory
  family as `plans.jsonl`). Explicit states `queued → claimed → running
  → succeeded | failed | cancelled`; atomic claims via `BEGIN IMMEDIATE`
  with a per-worker `--max-claims` bound; claim leases renewed by
  `heartbeat`; `sweep` recovers tasks whose lease expired without a
  heartbeat (re-queued while `--max-attempts` remain, `failed` when
  exhausted); `--key` idempotent resubmission; `cancel`; `status`;
  `collect` gathers terminal results before the aggregate report and
  stamps them (`--batch` scoping, `incomplete` stragglers, `--strict`
  exit 1). Payloads/results/errors are screened for secret-looking
  material at write time — the queue never stores credentials. The queue
  records and locks only; it never spawns workers or runs task commands.

- `queue fanout <repo> --branch NAME [--worktree PATH] [--files ...]` —
  submit a unit of work targeting an isolated git worktree: takes a
  physical lock on `(repo, worktree)` and `(repo, branch)` inside the
  task-insert transaction (released when the task goes terminal; a
  re-queued retry keeps its assignment) and refuses submissions whose
  declared `files` collide with a live task's on the same repo, reusing
  the planner's `file_collisions` semantics. Does not run `git worktree
  add` — the claiming worker creates the worktree at the recorded path.

- `plan --files FILE[,FILE...]` — declare plan units directly as a file
  list: one constrained unit per file (echoed under `units`), sets
  `independent_units` to the file count (overrides with a warning), runs
  the same collision check as `units_detail`. Mutually exclusive with
  spec `units_detail` and `--from-triggers`.

- `plan --from-triggers DIR [--event NAME]` consumes PAS trigger
  manifests (`*.{yaml,yml,json}`, devin-skill-catalog §8 format) as plan
  input: each enabled manifest is one work unit, `after`/`requires.
  artifacts` resolve into the DAG ordering echoed under `units` in the
  plan. Missing dependencies and cycles fail loudly; `action.command`
  is never propagated (plans stay data-only). YAML is parsed by a strict
  stdlib subset — the package still has zero dependencies.

- `plan --emit-manifest` serializes a WorkerPlan as a PAS trigger
  manifest (devin-skill-catalog §8 format): deterministic
  `orchestrator.plan-<hash>` id, `Scheduled` (with `--schedule`) or any
  synthetic `--manifest-event`, `idempotency.disabled` per catalog rules,
  and an `action.command` that re-derives the plan via the read-only
  `plan` call — units stay inside the job description, never shell
  fragments. `--format yaml|json`, `--out FILE`, `--manifest-id`,
  `--severity`.



- `record` now emits a per-run `metrics.json` under `metrics/<run_id>.json`
  beside `plans.jsonl` — always (no opt-out), local observability only.
  Optional `--run-id`, `--session-id`, `--duration`, `--started` and
  `--units` flags feed run/session ids, timestamps, total duration and
  per-unit status/duration detail; the file carries an `outcome_counts`
  roll-up. A metrics write failure warns on stderr but never breaks the
  record.

- `plan --cost-estimate` adds a `cost_estimate` block (or a `--explain`
  section): per-unit duration estimate in minutes and the sequential vs.
  fan-out critical path under the planned worker model. A planning label,
  not billing — new optional spec fields `est_unit_minutes` and
  `units_detail[].est_minutes` override the per-scope defaults.

### Changed

- README gains the generated `Part of the DEVIN ecosystem` block
  (track/nature/audience/interface rendered from the registry).

- `labeler.yml` is now a thin caller of the shared reusable workflow in `devin-powerups` (`@v1`); PR labeling behavior is unchanged.

- Install section now recommends pypi `uv tool install devin-fanout` (CLI stays `devin-orchestrator`) as the primary route, with `pipx`/source installs documented as alternatives.

### Fixed

- `devin_orchestrator.__version__` now reports `0.2.0`, matching
  `[project].version` in `pyproject.toml`; a `test_version.py` guard and
  the publish workflow's wheel check keep the two in sync.

## 0.2.0

- Merged the unpublished `devin-subagent-orchestrator` draft into this repo.
- SKILL.md rewritten: full policy (session-start triage, worker bounds, profiles,
  `.sessions.json` reuse, collection contract, worktree protection, publication
  gate) integrated with the deterministic planner step.
- Added `evals/evals.json` fixtures and `tests/test_skill_policy.py`.

## 0.1.0

- Initial release: deterministic fan-out planner, skill, always-on rule.
