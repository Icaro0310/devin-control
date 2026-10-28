"""Persistent task queue for the orchestrator — SQLite, local only.

The planner computes *how many* workers a fan-out may use; this module is
the durable store the coordinator and workers coordinate through. It is
the same class of local contract as ``plans.jsonl`` (state on disk,
nothing leaves the machine) — not a broker, not telemetry, and not a
second coordination store: claims, leases and results live in one SQLite
file beside the plan registry.

State machine::

    queued --claim--> claimed --heartbeat--> running
      |                 |                      |
      |                 +-- done/fail/cancel --+--> succeeded | failed | cancelled
      |
    claimed/running with an expired lease --sweep--> queued (retry left)
                                                   failed (attempts exhausted)

- Claims are atomic: one ``BEGIN IMMEDIATE`` transaction selects the
  oldest queued task and marks it claimed, so two workers can never hold
  the same task — concurrency is serialized by the database, not by
  promises.
- Claimed/running tasks carry a lease (``lease_expires_at``). ``sweep``
  is the abandoned-task recovery path: a task whose lease expired without
  a heartbeat is re-queued while attempts remain, or failed once
  ``max_attempts`` is exhausted — fail-closed, never left pretending to
  run.
- ``files``/``repo`` carry the same declared-path semantics as the
  planner's ``file_collisions`` check: submissions that overlap a live
  task's declared paths are refused.
- ``worktree_locks`` are physical (DB-enforced) exclusive locks on a
  ``(repo, worktree)`` path and ``(repo, branch)`` pair, held while the
  owning task is non-terminal and released inside the same transaction
  that finishes the task.
- Payloads and results are screened for secret-looking material on write
  and refused — queue logs must never contain secrets.

Stdlib only. WAL mode so readers never block a claiming worker.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from devin_orchestrator.planner import _norm_path, file_collisions

STATES = ("queued", "claimed", "running", "succeeded", "failed", "cancelled")
TERMINAL = ("succeeded", "failed", "cancelled")
HELD = ("claimed", "running")  # worker holds a lease

DEFAULT_LEASE_S = 300.0
DEFAULT_MAX_ATTEMPTS = 3
MAX_ATTEMPTS_CAP = 10
MAX_CLAIMS_PER_WORKER = 3  # mirrors the planner's MAX_WORKERS ceiling
MAX_ERROR_LEN = 2000
MAX_ID_LEN = 128

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id               TEXT PRIMARY KEY,
  idempotency_key  TEXT UNIQUE,
  batch            TEXT NOT NULL DEFAULT '',
  kind             TEXT NOT NULL DEFAULT 'task',
  status           TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','claimed','running',
                                     'succeeded','failed','cancelled')),
  repo             TEXT NOT NULL DEFAULT '',
  files            TEXT NOT NULL DEFAULT '[]',
  payload          TEXT NOT NULL DEFAULT '{}',
  result           TEXT,
  error            TEXT,
  priority         INTEGER NOT NULL DEFAULT 0,
  attempts         INTEGER NOT NULL DEFAULT 0,
  max_attempts     INTEGER NOT NULL DEFAULT 3,
  worker_id        TEXT,
  created_at       REAL NOT NULL,
  claimed_at       REAL,
  lease_expires_at REAL,
  heartbeat_at     REAL,
  finished_at      REAL,
  collected_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status
  ON tasks(status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch, status);
CREATE INDEX IF NOT EXISTS idx_tasks_lease
  ON tasks(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_tasks_repo ON tasks(repo, status);

CREATE TABLE IF NOT EXISTS workers (
  id         TEXT PRIMARY KEY,
  last_seen  REAL NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active',
  max_claims INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS worktree_locks (
  repo        TEXT NOT NULL,
  worktree    TEXT NOT NULL,
  branch      TEXT NOT NULL DEFAULT '',
  task_id     TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  acquired_at REAL NOT NULL,
  PRIMARY KEY (repo, worktree)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lock_branch
  ON worktree_locks(repo, branch) WHERE branch != '';
"""


# ---------------------------------------------------------------------------
# secrets — logs must never carry them
# ---------------------------------------------------------------------------

# High-confidence token shapes only: known prefixes, JWTs and sensitive
# key=value pairs with a token-alphabet value. Conservative on purpose —
# the scan refuses what *looks like* a credential, not the word "token".
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)(?:api[_-]?key|token|secret|password|passwd|authorization)"
        r"[\"']?\s*[:=]\s*[\\\"']*[A-Za-z0-9_./+~-]{12,}"
        # the value may open with a backslash-escaped quote when the
        # secret sits inside a JSON-encoded string (\"token\":\"…\")
    ),
)


def _secret_like(text: str | None) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _SECRET_PATTERNS)


def _refuse_secrets(text: str | None, field: str) -> None:
    """Fail closed: a secret in a queue field would land in logs, status
    and collect output forever — refuse the write instead of masking it."""
    if _secret_like(text):
        raise ValueError(
            f"{field} looks like it carries a secret — refused; "
            "the queue never stores credentials, tokens or keys"
        )


def default_queue_path() -> Path:
    """``<config>/.devin-ecosystem/task-queue.db`` — the same ecosystem
    directory family as ``plans.jsonl``."""
    override = os.environ.get("DEVIN_ECOSYSTEM_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "task-queue.db"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "devin-ecosystem" / "task-queue.db"
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / ".devin-ecosystem" / "task-queue.db"


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the queue database.

    ``isolation_level=None`` keeps sqlite3 in autocommit so multi-statement
    mutations take an explicit ``BEGIN IMMEDIATE`` — no implicit
    transaction fights the claim handshake.
    """
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _check_id(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_ID_LEN
        or value != value.strip()
    ):
        raise ValueError(
            f"{field} must be a non-empty string up to {MAX_ID_LEN} "
            "chars without leading/trailing whitespace"
        )
    return value


def _check_attempts(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not (1 <= value <= MAX_ATTEMPTS_CAP)
    ):
        raise ValueError(
            f"max_attempts must be an integer 1..{MAX_ATTEMPTS_CAP}"
        )
    return value


def _check_files(files: Any) -> list[str]:
    if files is None:
        return []
    if not isinstance(files, list) or not all(
        isinstance(f, str) and f for f in files
    ):
        raise ValueError("files must be a list of non-empty strings")
    return [_norm_path(f) for f in files]


def _task_doc(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "batch": row["batch"],
        "kind": row["kind"],
        "status": row["status"],
        "repo": row["repo"],
        "files": json.loads(row["files"]),
        "payload": json.loads(row["payload"]),
        "result": json.loads(row["result"]) if row["result"] else None,
        "error": row["error"],
        "priority": row["priority"],
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "worker_id": row["worker_id"],
        "created_at": row["created_at"],
        "claimed_at": row["claimed_at"],
        "lease_expires_at": row["lease_expires_at"],
        "heartbeat_at": row["heartbeat_at"],
        "finished_at": row["finished_at"],
        "collected_at": row["collected_at"],
    }


def _get(con: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown task: {task_id}")
    return row


def _upsert_worker(
    con: sqlite3.Connection, worker_id: str, max_claims: int, now: float
) -> None:
    con.execute(
        "INSERT INTO workers(id, last_seen, status, max_claims)"
        " VALUES(?,?, 'active', ?)"
        " ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,"
        " status='active', max_claims=excluded.max_claims",
        (worker_id, now, max_claims),
    )


def _collision_blockers(
    con: sqlite3.Connection, repo: str, files: list[str], new_id: str
) -> list[dict[str, Any]]:
    """``file_collisions`` against every *live* task on the same repo —
    the planner's disjointness check applied to the queue's real work."""
    if not files:
        return []
    units = [{"id": new_id, "files": files}]
    for row in con.execute(
        "SELECT id, files FROM tasks WHERE repo=? AND status IN"
        " ('queued','claimed','running')",
        (repo,),
    ):
        units.append({"id": row["id"], "files": json.loads(row["files"])})
    return [
        c for c in file_collisions(units) if new_id in c["units"]
    ]


# ---------------------------------------------------------------------------
# writes — submit / fanout
# ---------------------------------------------------------------------------


def submit(
    con: sqlite3.Connection,
    payload: dict[str, Any] | None = None,
    *,
    kind: str = "task",
    files: list[str] | None = None,
    repo: str = "",
    batch: str = "",
    idempotency_key: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    priority: int = 0,
    now: float | None = None,
) -> dict[str, Any]:
    """Queue one task; returns ``{"id": ..., "deduplicated": bool}``.

    ``idempotency_key`` makes resubmission safe: a key that already
    exists returns the original task instead of a duplicate row. Declared
    ``files`` collide-checked against live tasks on the same ``repo`` —
    a submission that overlaps a running unit is refused, not warned.
    """
    now = time.time() if now is None else now
    if kind == "worktree":
        raise ValueError(
            "kind='worktree' is reserved for `queue fanout` — "
            "worktree tasks need a lock, submit() cannot take one"
        )
    _check_id(kind, "kind")
    if batch:
        _check_id(batch, "batch")
    if idempotency_key is not None:
        _check_id(idempotency_key, "idempotency_key")
    max_attempts = _check_attempts(max_attempts)
    files = _check_files(files)
    payload = payload or {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")  # noqa: TRY004 - callers/tests catch ValueError
    blob = json.dumps(payload, separators=(",", ":"))
    _refuse_secrets(blob, "payload")
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise ValueError("priority must be an integer")  # noqa: TRY004 - callers/tests catch ValueError

    con.execute("BEGIN IMMEDIATE")
    try:
        if idempotency_key is not None:
            row = con.execute(
                "SELECT id FROM tasks WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                con.execute("COMMIT")
                return {"id": row["id"], "deduplicated": True}
        tid = uuid.uuid4().hex[:16]
        collisions = _collision_blockers(con, repo, files, tid)
        if collisions:
            con.execute("ROLLBACK")
            detail = ", ".join(
                f"task {c['units'][1] if c['units'][0] == tid else c['units'][0]}"
                f" also touches {c['path']}"
                for c in collisions[:5]
            )
            raise ValueError(
                f"file collision with live task(s) on {repo or '<no repo>'}: "
                f"{detail} — units are not disjoint"
            )
        con.execute(
            "INSERT INTO tasks(id, idempotency_key, batch, kind, repo,"
            " files, payload, priority, max_attempts, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                idempotency_key,
                batch,
                kind,
                repo,
                json.dumps(files),
                blob,
                priority,
                max_attempts,
                now,
            ),
        )
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return {"id": tid, "deduplicated": False}


def submit_fanout(
    con: sqlite3.Connection,
    repo: str,
    branch: str,
    files: list[str] | None = None,
    *,
    worktree: str | None = None,
    payload: dict[str, Any] | None = None,
    batch: str = "",
    idempotency_key: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    priority: int = 0,
    now: float | None = None,
) -> dict[str, Any]:
    """Submit one unit of work targeting an isolated git worktree.

    The physical lock is a ``worktree_locks`` row taken inside the same
    transaction as the task insert — ``(repo, worktree)`` and
    ``(repo, branch)`` are unique, so two queued units can never target
    the same worktree or branch. ``files`` run the same
    ``file_collisions`` disjointness check the planner applies. The lock
    is released only when the owning task goes terminal (done, fail with
    retries exhausted, cancel, or sweep exhaustion); a re-queued retry
    keeps its worktree assignment.

    The queue records and locks — it does not run ``git worktree add``;
    the claiming worker creates the worktree at the recorded path.
    """
    now = time.time() if now is None else now
    repo_path = Path(repo).expanduser()
    if not repo_path.is_dir():
        raise ValueError(f"repo is not a directory: {repo}")
    repo_abs = str(repo_path.resolve())
    _check_id(branch, "branch")
    wt = (
        Path(worktree).expanduser()
        if worktree
        else repo_path.parent / ".devin-worktrees" / re.sub(
            r"[^A-Za-z0-9._-]+", "-", branch
        ).strip("-.")
    )
    if not wt.is_absolute():
        wt = Path.cwd() / wt
    worktree_abs = str(wt.resolve())
    files = _check_files(files)
    max_attempts = _check_attempts(max_attempts)
    if batch:
        _check_id(batch, "batch")
    if idempotency_key is not None:
        _check_id(idempotency_key, "idempotency_key")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise ValueError("priority must be an integer")  # noqa: TRY004 - callers/tests catch ValueError
    # caller payload spreads first so repo/worktree/branch/files can
    # never be spoofed — the lock and the task doc always agree
    body = {
        **(payload or {}),
        "repo": repo_abs,
        "worktree": worktree_abs,
        "branch": branch,
        "files": files,
    }
    blob = json.dumps(body, separators=(",", ":"))
    _refuse_secrets(blob, "payload")

    con.execute("BEGIN IMMEDIATE")
    try:
        if idempotency_key is not None:
            row = con.execute(
                "SELECT id FROM tasks WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                con.execute("COMMIT")
                return {"id": row["id"], "deduplicated": True}
        held = con.execute(
            "SELECT worktree, branch, task_id FROM worktree_locks"
            " WHERE repo=? AND (worktree=? OR branch=?)",
            (repo_abs, worktree_abs, branch),
        ).fetchone()
        if held is not None:
            con.execute("ROLLBACK")
            what = (
                "worktree" if held["worktree"] == worktree_abs else "branch"
            )
            raise ValueError(
                f"{what} already locked by task {held['task_id']}: "
                f"{held['worktree'] if what == 'worktree' else held['branch']}"
            )
        tid = uuid.uuid4().hex[:16]
        collisions = _collision_blockers(con, repo_abs, files, tid)
        if collisions:
            con.execute("ROLLBACK")
            detail = ", ".join(
                f"task {c['units'][1] if c['units'][0] == tid else c['units'][0]}"
                f" also touches {c['path']}"
                for c in collisions[:5]
            )
            raise ValueError(
                f"file collision with live task(s) on {repo_abs}: "
                f"{detail} — units are not disjoint"
            )
        con.execute(
            "INSERT INTO tasks(id, idempotency_key, batch, kind, repo,"
            " files, payload, priority, max_attempts, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                idempotency_key,
                batch,
                "worktree",
                repo_abs,
                json.dumps(files),
                blob,
                priority,
                max_attempts,
                now,
            ),
        )
        con.execute(
            "INSERT INTO worktree_locks(repo, worktree, branch, task_id,"
            " acquired_at) VALUES(?,?,?,?,?)",
            (repo_abs, worktree_abs, branch, tid, now),
        )
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return {"id": tid, "deduplicated": False, "worktree": worktree_abs}


# ---------------------------------------------------------------------------
# claims / heartbeats
# ---------------------------------------------------------------------------


def claim(
    con: sqlite3.Connection,
    worker_id: str,
    *,
    kind: str | None = None,
    max_claims: int = 1,
    lease_s: float = DEFAULT_LEASE_S,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the oldest queued task for ``worker_id``.

    One ``BEGIN IMMEDIATE`` transaction does limit-check → select →
    update, so two workers racing on the same database file can never
    claim the same task. The claim carries a lease
    (``lease_expires_at = now + lease_s``); the worker must ``heartbeat``
    before it lapses or ``sweep`` will recover the task.
    ``max_claims`` bounds how many tasks one worker may hold at once
    (hard cap ``MAX_CLAIMS_PER_WORKER``).
    """
    now = time.time() if now is None else now
    _check_id(worker_id, "worker")
    if (
        not isinstance(max_claims, int)
        or isinstance(max_claims, bool)
        or not (1 <= max_claims <= MAX_CLAIMS_PER_WORKER)
    ):
        raise ValueError(
            f"max_claims must be an integer 1..{MAX_CLAIMS_PER_WORKER}"
        )
    if not isinstance(lease_s, (int, float)) or lease_s <= 0:
        raise ValueError("lease_s must be a positive number of seconds")
    if kind is not None:
        _check_id(kind, "kind")

    con.execute("BEGIN IMMEDIATE")
    try:
        held = con.execute(
            "SELECT COUNT(*) n FROM tasks WHERE worker_id=?"
            " AND status IN ('claimed','running')",
            (worker_id,),
        ).fetchone()["n"]
        if held >= max_claims:
            con.execute("COMMIT")
            return None
        sql = (
            "SELECT id FROM tasks WHERE status='queued'"
            + (" AND kind=?" if kind else "")
            + " ORDER BY priority DESC, created_at LIMIT 1"
        )
        row = con.execute(sql, (kind,) if kind else ()).fetchone()
        if row is None:
            con.execute("COMMIT")
            _upsert_worker(con, worker_id, max_claims, now)
            return None
        con.execute(
            "UPDATE tasks SET status='claimed', worker_id=?,"
            " claimed_at=?, lease_expires_at=?, heartbeat_at=NULL,"
            " attempts=attempts+1, finished_at=NULL, error=NULL"
            " WHERE id=?",
            (worker_id, now, now + lease_s, row["id"]),
        )
        _upsert_worker(con, worker_id, max_claims, now)
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return _task_doc(_get(con, row["id"]))


def heartbeat(
    con: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    *,
    lease_s: float = DEFAULT_LEASE_S,
    now: float | None = None,
) -> dict[str, Any]:
    """Renew a held task's lease; the first heartbeat also marks it
    ``running`` (a claimed task whose worker is provably alive and
    executing). Heartbeats are the evidence ``sweep`` looks for."""
    now = time.time() if now is None else now
    _check_id(worker_id, "worker")
    if not isinstance(lease_s, (int, float)) or lease_s <= 0:
        raise ValueError("lease_s must be a positive number of seconds")
    con.execute("BEGIN IMMEDIATE")
    try:
        row = _get(con, task_id)
        if row["status"] not in HELD:
            con.execute("ROLLBACK")
            raise ValueError(
                f"task {task_id} is {row['status']} — "
                "only claimed/running tasks take heartbeats"
            )
        if row["worker_id"] != worker_id:
            con.execute("ROLLBACK")
            raise ValueError(
                f"task {task_id} is held by {row['worker_id']}, "
                f"not {worker_id}"
            )
        con.execute(
            "UPDATE tasks SET status='running', heartbeat_at=?,"
            " lease_expires_at=? WHERE id=?",
            (now, now + lease_s, task_id),
        )
        con.execute(
            "INSERT INTO workers(id, last_seen, status) VALUES(?,?, 'active')"
            " ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen",
            (worker_id, now),
        )
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return _task_doc(_get(con, task_id))


# ---------------------------------------------------------------------------
# terminal transitions
# ---------------------------------------------------------------------------


def _finish_checks(row: sqlite3.Row, task_id: str, worker_id: str) -> None:
    if row["status"] not in HELD:
        raise ValueError(
            f"task {task_id} is {row['status']} — "
            "only claimed/running tasks can finish"
        )
    if row["worker_id"] != worker_id:
        raise ValueError(
            f"task {task_id} is held by {row['worker_id']}, not {worker_id}"
        )


def done(
    con: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    result: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Mark a held task ``succeeded`` and release its worktree lock."""
    now = time.time() if now is None else now
    _check_id(worker_id, "worker")
    blob = json.dumps(result, separators=(",", ":")) if result is not None else None
    _refuse_secrets(blob, "result")
    con.execute("BEGIN IMMEDIATE")
    try:
        _finish_checks(_get(con, task_id), task_id, worker_id)
        con.execute(
            "UPDATE tasks SET status='succeeded', result=?, error=NULL,"
            " finished_at=?, lease_expires_at=NULL WHERE id=?",
            (blob, now, task_id),
        )
        con.execute("DELETE FROM worktree_locks WHERE task_id=?", (task_id,))
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return _task_doc(_get(con, task_id))


def fail(
    con: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    error: str,
    *,
    final: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Mark a held task failed — or hand it back to ``queued`` for a
    bounded retry while attempts remain.

    ``final=True`` forces the terminal state even with retries left.
    The worktree lock is released only on the terminal transition; a
    re-queued task keeps its worktree assignment for the next attempt.
    """
    now = time.time() if now is None else now
    _check_id(worker_id, "worker")
    if not isinstance(error, str) or not error.strip():
        raise ValueError("error must be a non-empty string")
    error = error.strip()[:MAX_ERROR_LEN]
    _refuse_secrets(error, "error")
    con.execute("BEGIN IMMEDIATE")
    try:
        row = _get(con, task_id)
        _finish_checks(row, task_id, worker_id)
        terminal = final or row["attempts"] >= row["max_attempts"]
        if terminal:
            con.execute(
                "UPDATE tasks SET status='failed', error=?,"
                " finished_at=?, lease_expires_at=NULL WHERE id=?",
                (error, now, task_id),
            )
            con.execute(
                "DELETE FROM worktree_locks WHERE task_id=?", (task_id,)
            )
        else:
            con.execute(
                "UPDATE tasks SET status='queued', error=?, worker_id=NULL,"
                " lease_expires_at=NULL, heartbeat_at=NULL WHERE id=?",
                (error, task_id),
            )
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return _task_doc(_get(con, task_id))


def cancel(
    con: sqlite3.Connection,
    task_id: str,
    *,
    reason: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Cancel a non-terminal task — the coordinator's stop signal; any
    actor may cancel (unlike done/fail, which require the lease holder).
    Terminal tasks are untouched and returned unchanged."""
    now = time.time() if now is None else now
    if reason:
        _refuse_secrets(reason, "reason")
        reason = reason.strip()[:MAX_ERROR_LEN]
    con.execute("BEGIN IMMEDIATE")
    try:
        row = _get(con, task_id)
        if row["status"] not in TERMINAL:
            con.execute(
                "UPDATE tasks SET status='cancelled', error=?,"
                " finished_at=?, lease_expires_at=NULL WHERE id=?",
                (reason or None, now, task_id),
            )
            con.execute(
                "DELETE FROM worktree_locks WHERE task_id=?", (task_id,)
            )
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return _task_doc(_get(con, task_id))


# ---------------------------------------------------------------------------
# recovery — the orphan sweep
# ---------------------------------------------------------------------------


def sweep(
    con: sqlite3.Connection,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Recover tasks whose lease expired without a heartbeat.

    A worker that crashed mid-task leaves a claimed/running row behind;
    once ``lease_expires_at`` passes, the task is either re-queued for a
    bounded retry (attempts left) or marked ``failed`` (attempts
    exhausted) — fail-closed, never left pretending to still run.
    """
    now = time.time() if now is None else now
    con.execute("BEGIN IMMEDIATE")
    try:
        rows = con.execute(
            "SELECT id, attempts, max_attempts FROM tasks"
            " WHERE status IN ('claimed','running')"
            " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        requeued: list[str] = []
        failed: list[str] = []
        for r in rows:
            if r["attempts"] >= r["max_attempts"]:
                con.execute(
                    "UPDATE tasks SET status='failed',"
                    " error='lease expired without heartbeat; attempts"
                    " exhausted', finished_at=?, lease_expires_at=NULL"
                    " WHERE id=?",
                    (now, r["id"]),
                )
                con.execute(
                    "DELETE FROM worktree_locks WHERE task_id=?", (r["id"],)
                )
                failed.append(r["id"])
            else:
                con.execute(
                    "UPDATE tasks SET status='queued',"
                    " error='lease expired without heartbeat — re-queued',"
                    " worker_id=NULL, lease_expires_at=NULL,"
                    " heartbeat_at=NULL WHERE id=?",
                    (r["id"],),
                )
                requeued.append(r["id"])
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return {
        "scanned": len(rows),
        "requeued": requeued,
        "failed": failed,
        "ts": now,
    }


# ---------------------------------------------------------------------------
# reads — collect / status
# ---------------------------------------------------------------------------


def collect(
    con: sqlite3.Connection,
    *,
    batch: str | None = None,
    include_collected: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Gather finished task results before any aggregate report — the
    ``collect`` half of the plan's collect-before-report contract.

    Default scope: terminal tasks not yet collected (``--batch`` narrows
    it). Collected rows are stamped with ``collected_at`` so a second
    collect yields only new finishes; ``include_collected`` re-reads the
    whole terminal set. ``incomplete`` lists tasks still open, so the
    report can never silently omit a straggler.
    """
    now = time.time() if now is None else now
    where = "status IN ('succeeded','failed','cancelled')"
    params: list[Any] = []
    if batch is not None:
        where += " AND batch=?"
        params.append(batch)
    if not include_collected:
        where += " AND collected_at IS NULL"
    rows = con.execute(
        f"SELECT * FROM tasks WHERE {where} ORDER BY finished_at", params
    ).fetchall()
    open_rows = con.execute(
        "SELECT id, status FROM tasks WHERE status IN"
        " ('queued','claimed','running')"
        + (" AND batch=?" if batch is not None else ""),
        ([batch] if batch is not None else []),
    ).fetchall()
    by_status: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    for r in rows:
        doc = _task_doc(r)
        by_status[doc["status"]] = by_status.get(doc["status"], 0) + 1
        results.append(doc)
    if results:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.executemany(
                "UPDATE tasks SET collected_at=? WHERE id=?",
                [(now, r["id"]) for r in rows],
            )
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    return {
        "batch": batch,
        "complete": not open_rows,
        "incomplete": [dict(r) for r in open_rows],
        "total": len(results),
        "by_status": by_status,
        "results": results,
        "ts": now,
    }


def status(
    con: sqlite3.Connection,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Queue snapshot: counts per state, per-worker holds, worktree
    locks, and how many held tasks are already past their lease (what
    ``sweep`` would recover)."""
    now = time.time() if now is None else now
    counts = {
        r["status"]: r["n"]
        for r in con.execute(
            "SELECT status, COUNT(*) n FROM tasks GROUP BY status"
        )
    }
    for state in STATES:
        counts.setdefault(state, 0)
    by_kind = {
        r["kind"]: r["n"]
        for r in con.execute(
            "SELECT kind, COUNT(*) n FROM tasks WHERE status='queued'"
            " GROUP BY kind"
        )
    }
    workers = [
        {
            "id": r["id"],
            "last_seen": r["last_seen"],
            "held": r["held"],
            "max_claims": r["max_claims"],
        }
        for r in con.execute(
            "SELECT w.id, w.last_seen, w.max_claims,"
            " (SELECT COUNT(*) FROM tasks t WHERE t.worker_id=w.id"
            "  AND t.status IN ('claimed','running')) held"
            " FROM workers w ORDER BY w.last_seen DESC"
        )
    ]
    locks = [
        dict(r)
        for r in con.execute(
            "SELECT repo, worktree, branch, task_id, acquired_at"
            " FROM worktree_locks ORDER BY acquired_at"
        )
    ]
    expired = con.execute(
        "SELECT COUNT(*) n FROM tasks WHERE status IN ('claimed','running')"
        " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
        (now,),
    ).fetchone()["n"]
    batches = {
        r["batch"]: {"open": r["open"], "done": r["done"]}
        for r in con.execute(
            "SELECT batch,"
            " SUM(status IN ('queued','claimed','running')) open,"
            " SUM(status IN ('succeeded','failed','cancelled')) done"
            " FROM tasks WHERE batch != '' GROUP BY batch"
        )
    }
    return {
        "counts": counts,
        "queued": counts["queued"],
        "queued_by_kind": by_kind,
        "held": counts["claimed"] + counts["running"],
        "lease_expired": expired,
        "workers": workers,
        "locks": locks,
        "batches": batches,
        "ts": now,
    }
