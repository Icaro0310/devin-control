"""Queue tests — the persistent SQLite task queue (OR-queue).

Every test runs against a tmp database; the module contract (atomic
claims, leases, bounded retries, worktree locks, no secrets) is exercised
through ``queue_db`` directly plus a CLI surface pass at the end.
"""

from __future__ import annotations

import json

import pytest
from devin_orchestrator import queue_db
from devin_orchestrator.cli import main


@pytest.fixture
def con(tmp_path):
    c = queue_db.connect(tmp_path / "q.db")
    yield c
    c.close()


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    return d


# -- submit -----------------------------------------------------------------


def test_submit_returns_id_and_queued(con):
    out = queue_db.submit(con, {"what": "build api"})
    assert out["id"] and out["deduplicated"] is False
    assert queue_db.status(con)["counts"]["queued"] == 1


def test_submit_idempotency_key_deduplicates(con):
    a = queue_db.submit(con, {"x": 1}, idempotency_key="k1")
    b = queue_db.submit(con, {"x": 2}, idempotency_key="k1")
    assert a["id"] == b["id"]
    assert b["deduplicated"] is True
    assert queue_db.status(con)["queued"] == 1


def test_submit_validates_input(con):
    with pytest.raises(ValueError):
        queue_db.submit(con, ["not", "a", "dict"])
    with pytest.raises(ValueError):
        queue_db.submit(con, {}, files="src/")  # not a list
    with pytest.raises(ValueError):
        queue_db.submit(con, {}, files=[""])  # empty path
    with pytest.raises(ValueError):
        queue_db.submit(con, {}, max_attempts=0)
    with pytest.raises(ValueError):
        queue_db.submit(con, {}, max_attempts=99)
    with pytest.raises(ValueError):
        queue_db.submit(con, {}, kind="worktree")  # reserved for fanout
    with pytest.raises(ValueError):
        queue_db.submit(con, {}, idempotency_key="  ")  # blank key
    with pytest.raises(ValueError):
        queue_db.submit(con, {}, priority="high")  # priority is an int


def test_submit_file_collision_refused(con):
    queue_db.submit(con, {}, files=["src/api/"], repo="/r")
    with pytest.raises(ValueError, match="collision"):
        queue_db.submit(con, {}, files=["src/api/x.py"], repo="/r")
    # different repo scope: same path is fine
    other = queue_db.submit(con, {}, files=["src/api/x.py"], repo="/r2")
    assert other["id"]


def test_submit_collision_ignores_terminal_tasks(con):
    queue_db.submit(con, {}, files=["src/"], repo="/r")
    t = queue_db.claim(con, "w", now=1000)
    queue_db.done(con, t["id"], "w", now=1001)
    # the finished task's files no longer collide
    assert queue_db.submit(con, {}, files=["src/x.py"], repo="/r")["id"]


# -- claim ------------------------------------------------------------------


def test_claim_oldest_first_and_priority(con):
    queue_db.submit(con, {"n": "a"})
    queue_db.submit(con, {"n": "b"}, priority=5)
    queue_db.submit(con, {"n": "c"})
    first = queue_db.claim(con, "w")
    assert first["payload"]["n"] == "b"  # priority wins
    second = queue_db.claim(con, "w", max_claims=3)
    assert second["payload"]["n"] == "a"  # then FIFO
    assert second["status"] == "claimed"
    assert second["attempts"] == 1
    assert second["lease_expires_at"] > second["claimed_at"]


def test_claim_empty_returns_none(con):
    assert queue_db.claim(con, "w") is None


def test_claim_kind_filter(con):
    queue_db.submit(con, {}, kind="docs")
    assert queue_db.claim(con, "w", kind="code") is None
    assert queue_db.claim(con, "w", kind="docs")["kind"] == "docs"


def test_claim_respects_max_claims(con):
    queue_db.submit(con, {})
    queue_db.submit(con, {})
    assert queue_db.claim(con, "w") is not None
    # default max_claims=1: worker at limit gets nothing
    assert queue_db.claim(con, "w") is None
    # another worker can still claim
    assert queue_db.claim(con, "w2") is not None


def test_claim_max_claims_bounds(con):
    with pytest.raises(ValueError):
        queue_db.claim(con, "w", max_claims=0)
    with pytest.raises(ValueError):
        queue_db.claim(
            con, "w", max_claims=queue_db.MAX_CLAIMS_PER_WORKER + 1)


def test_claim_is_atomic_across_connections(tmp_path):
    """Two connections racing on the same file can never claim the same
    task — BEGIN IMMEDIATE serializes the select+update."""
    db = tmp_path / "race.db"
    c1, c2 = queue_db.connect(db), queue_db.connect(db)
    c2.execute("PRAGMA busy_timeout=50")  # don't hang the test on the lock
    queue_db.submit(c1, {})
    queue_db.submit(c1, {})
    # while c1 holds a write transaction, c2's claim cannot interleave
    c1.execute("BEGIN IMMEDIATE")
    try:
        import sqlite3
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            queue_db.claim(c2, "w2")
    finally:
        c1.execute("ROLLBACK")
    a = queue_db.claim(c1, "w1")
    b = queue_db.claim(c2, "w2")
    assert a["id"] != b["id"]
    assert queue_db.claim(c2, "w3") is None  # queue drained
    c2.close()


def test_claim_rejects_bad_worker(con):
    queue_db.submit(con, {})
    with pytest.raises(ValueError):
        queue_db.claim(con, " ")


# -- heartbeat ---------------------------------------------------------------


def test_heartbeat_marks_running_and_renews_lease(con):
    tid = queue_db.submit(con, {})["id"]
    t = queue_db.claim(con, "w", lease_s=100, now=1000)
    t = queue_db.heartbeat(con, tid, "w", lease_s=200, now=1050)
    assert t["status"] == "running"
    assert t["heartbeat_at"] == 1050
    assert t["lease_expires_at"] == 1250


def test_heartbeat_requires_lease_holder(con):
    tid = queue_db.submit(con, {})["id"]
    queue_db.claim(con, "w1")
    with pytest.raises(ValueError, match="held by w1"):
        queue_db.heartbeat(con, tid, "w2")
    with pytest.raises(ValueError, match="queued"):
        queue_db.heartbeat(con, queue_db.submit(con, {})["id"], "w1")


# -- done / fail / cancel ----------------------------------------------------


def test_done_success_and_wrong_worker(con):
    tid = queue_db.submit(con, {})["id"]
    queue_db.claim(con, "w1")
    with pytest.raises(ValueError, match="held by w1"):
        queue_db.done(con, tid, "w2")
    t = queue_db.done(con, tid, "w1", {"sha": "abc"})
    assert t["status"] == "succeeded"
    assert t["result"] == {"sha": "abc"}
    assert t["finished_at"] is not None


def test_fail_requeues_then_terminal(con):
    tid = queue_db.submit(con, {}, max_attempts=2)["id"]
    queue_db.claim(con, "w")
    t = queue_db.fail(con, tid, "w", "boom")
    assert t["status"] == "queued"  # attempt 1 of 2 — retry left
    assert t["error"] == "boom"
    assert t["worker_id"] is None
    t = queue_db.claim(con, "w2")
    assert t["attempts"] == 2
    t = queue_db.fail(con, tid, "w2", "boom again")
    assert t["status"] == "failed"  # attempts exhausted


def test_fail_final_skips_retries(con):
    tid = queue_db.submit(con, {})["id"]
    queue_db.claim(con, "w")
    t = queue_db.fail(con, tid, "w", "unrecoverable", final=True)
    assert t["status"] == "failed"


def test_fail_on_unheld_task(con):
    tid = queue_db.submit(con, {})["id"]
    with pytest.raises(ValueError, match="queued"):
        queue_db.fail(con, tid, "w", "nope")


def test_cancel_from_any_state(con):
    tid = queue_db.submit(con, {})["id"]
    t = queue_db.cancel(con, tid, reason="superseded")
    assert t["status"] == "cancelled"
    # cancelling a terminal task is a no-op, not an error
    t = queue_db.cancel(con, tid)
    assert t["status"] == "cancelled"


# -- sweep (orphan recovery) -------------------------------------------------


def test_sweep_requeues_expired_lease(con):
    tid = queue_db.submit(con, {})["id"]
    queue_db.claim(con, "w", lease_s=10, now=1000)
    out = queue_db.sweep(con, now=1011)
    assert out["requeued"] == [tid]
    t = queue_db._get(con, tid)
    assert t["status"] == "queued"
    assert t["worker_id"] is None
    assert "lease expired" in t["error"]


def test_sweep_leaves_fresh_leases_alone(con):
    queue_db.submit(con, {})
    queue_db.claim(con, "w", lease_s=100, now=1000)
    assert queue_db.sweep(con, now=1050)["scanned"] == 0


def test_sweep_fails_when_attempts_exhausted(con):
    tid = queue_db.submit(con, {}, max_attempts=1)["id"]
    queue_db.claim(con, "w", lease_s=10, now=1000)
    out = queue_db.sweep(con, now=1011)
    assert out["failed"] == [tid]
    t = queue_db._get(con, tid)
    assert t["status"] == "failed"
    assert "attempts exhausted" in t["error"]


def test_sweep_does_not_touch_terminal(con):
    tid = queue_db.submit(con, {})["id"]
    queue_db.claim(con, "w", lease_s=10, now=1000)
    queue_db.done(con, tid, "w", now=1005)
    assert queue_db.sweep(con, now=2000)["scanned"] == 0


# -- collect (before the aggregate report) ------------------------------------


def test_collect_gathers_terminal_once(con):
    a = queue_db.submit(con, {}, batch="b")["id"]
    queue_db.submit(con, {}, batch="b")
    queue_db.claim(con, "w")
    queue_db.done(con, a, "w")
    doc = queue_db.collect(con, batch="b")
    assert doc["total"] == 1
    assert doc["by_status"] == {"succeeded": 1}
    assert doc["complete"] is False  # one task still queued
    assert len(doc["incomplete"]) == 1
    # collected tasks are stamped — a second collect yields only new ones
    doc2 = queue_db.collect(con, batch="b")
    assert doc2["total"] == 0
    doc3 = queue_db.collect(con, batch="b", include_collected=True)
    assert doc3["total"] == 1


def test_collect_batch_scope(con):
    """``--batch`` scopes the collection: only that fan-out's results and
    only its stragglers are reported."""
    a = queue_db.submit(con, {}, batch="a")["id"]
    b = queue_db.submit(con, {}, batch="b")["id"]
    queue_db.claim(con, "w", max_claims=2)
    queue_db.claim(con, "w2")
    queue_db.done(con, a, "w")
    queue_db.done(con, b, "w2")
    doc = queue_db.collect(con, batch="a")
    assert doc["batch"] == "a"
    assert doc["total"] == 1
    assert doc["results"][0]["id"] == a
    doc = queue_db.collect(con)  # no batch: everything else terminal
    assert doc["total"] == 1
    assert doc["results"][0]["id"] == b


def test_collect_complete_when_all_terminal(con):
    tid = queue_db.submit(con, {})["id"]
    queue_db.claim(con, "w")
    queue_db.done(con, tid, "w")
    doc = queue_db.collect(con)
    assert doc["complete"] is True
    assert doc["incomplete"] == []


# -- status ------------------------------------------------------------------


def test_status_counts_and_workers(con):
    queue_db.submit(con, {})
    queue_db.submit(con, {})
    queue_db.claim(con, "w1")
    st = queue_db.status(con)
    assert st["counts"]["queued"] == 1
    assert st["counts"]["claimed"] == 1
    assert st["held"] == 1
    assert st["workers"][0]["id"] == "w1"
    assert st["workers"][0]["held"] == 1
    assert st["lease_expired"] == 0


def test_status_reports_expired_leases(con):
    queue_db.submit(con, {})
    queue_db.claim(con, "w", lease_s=5, now=1000)
    assert queue_db.status(con, now=2000)["lease_expired"] == 1


# -- fanout (worktree submit) -------------------------------------------------


def test_fanout_locks_worktree_and_branch(con, repo):
    out = queue_db.submit_fanout(
        con, str(repo), "feat-a", files=["src/api/"])
    assert out["worktree"].endswith("feat-a")
    st = queue_db.status(con)
    assert len(st["locks"]) == 1
    assert st["locks"][0]["branch"] == "feat-a"
    # same branch, different worktree name -> refused
    with pytest.raises(ValueError, match="already locked"):
        queue_db.submit_fanout(
            con, str(repo), "feat-a", worktree=str(repo / "wt2"))
    # same worktree, different branch -> refused
    with pytest.raises(ValueError, match="already locked"):
        queue_db.submit_fanout(
            con, str(repo), "feat-b",
            worktree=out["worktree"])


def test_fanout_file_collision(con, repo):
    queue_db.submit_fanout(con, str(repo), "a", files=["src/api/"])
    with pytest.raises(ValueError, match="collision"):
        queue_db.submit_fanout(con, str(repo), "b", files=["src/api/x.py"])
    ok = queue_db.submit_fanout(con, str(repo), "b", files=["src/ui/"])
    assert ok["id"]


def test_fanout_lock_released_on_terminal(con, repo):
    out = queue_db.submit_fanout(con, str(repo), "a", worktree=str(repo / "wt"))
    queue_db.claim(con, "w")
    queue_db.done(con, out["id"], "w")
    assert queue_db.status(con)["locks"] == []
    # the same worktree+branch can be reused by a new unit
    again = queue_db.submit_fanout(
        con, str(repo), "a", worktree=str(repo / "wt"))
    assert again["id"]


def test_fanout_requeue_keeps_lock(con, repo):
    out = queue_db.submit_fanout(con, str(repo), "a")
    queue_db.claim(con, "w", lease_s=5, now=1000)
    queue_db.sweep(con, now=2000)  # re-queued, attempts left
    # lock survives the retry — the worktree assignment is preserved
    assert len(queue_db.status(con)["locks"]) == 1
    with pytest.raises(ValueError, match="already locked"):
        queue_db.submit_fanout(con, str(repo), "b", worktree=out["worktree"])


def test_fanout_requires_existing_repo(con, tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        queue_db.submit_fanout(con, str(tmp_path / "nope"), "a")


def test_fanout_idempotent(con, repo):
    a = queue_db.submit_fanout(con, str(repo), "a", idempotency_key="k")
    b = queue_db.submit_fanout(con, str(repo), "a", idempotency_key="k")
    assert a["id"] == b["id"] and b["deduplicated"]


# -- secrets never reach the log ---------------------------------------------


def test_submit_refuses_secret_payloads(con):
    for bad in (
        {"token": "sk-abcdefghijklmnop1234567890"},
        {"auth": "ghp_0123456789abcdefghijklmnop"},
        {"k": "AKIAIOSFODNN7EXAMPLE"},
        {"note": 'token="supersecretvalue123"'},
    ):
        with pytest.raises(ValueError, match="secret"):
            queue_db.submit(con, bad)
    assert queue_db.status(con)["queued"] == 0


def test_done_result_and_fail_error_screened(con):
    tid = queue_db.submit(con, {})["id"]
    queue_db.claim(con, "w")
    with pytest.raises(ValueError, match="secret"):
        queue_db.done(con, tid, "w", {"t": "xoxb-1234567890-abcdefghij"})
    with pytest.raises(ValueError, match="secret"):
        queue_db.fail(con, tid, "w", "leaked ghp_0123456789abcdefghijklmnop")
    # benign text passes the screen
    assert queue_db.done(con, tid, "w", {"note": "set token env var"})


# -- CLI surface -------------------------------------------------------------


def test_cli_queue_lifecycle(tmp_path, capsys):
    db = tmp_path / "q.db"
    assert main(["queue", "submit", '{"a":1}', "--queue", str(db)]) == 0
    tid = json.loads(capsys.readouterr().out)["id"]
    assert main(["queue", "claim", "--worker", "w",
                 "--queue", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == tid
    assert main(["queue", "done", tid, "--worker", "w",
                 "--result", '{"ok":true}', "--queue", str(db)]) == 0
    capsys.readouterr()
    assert main(["queue", "collect", "--queue", str(db)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["complete"] and doc["total"] == 1
    assert main(["queue", "status", "--queue", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["succeeded"] == 1


def test_cli_queue_errors(tmp_path, capsys):
    db = tmp_path / "q.db"
    assert main(["queue", "submit", "{bad json", "--queue", str(db)]) == 2
    assert main(["queue", "done", "nope", "--worker", "w",
                 "--queue", str(db)]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_collect_strict_exit_code(tmp_path, capsys):
    db = tmp_path / "q.db"
    main(["queue", "submit", "{}", "--queue", str(db)])
    capsys.readouterr()
    assert main(["queue", "collect", "--strict", "--queue", str(db)]) == 1


def test_cli_queue_sweep_and_cancel(tmp_path, capsys):
    db = tmp_path / "q.db"
    main(["queue", "submit", "{}", "--queue", str(db)])
    tid = json.loads(capsys.readouterr().out)["id"]
    assert main(["queue", "cancel", tid, "--reason", "no longer needed",
                 "--queue", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"
    assert main(["queue", "sweep", "--queue", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["scanned"] == 0


# -- plan --files -------------------------------------------------------------


def test_plan_files_builds_units(capsys):
    rc = main(["plan", '{"kind":"implementation"}',
               "--files", "src/a.py,src/b.py", "--files", "src/c.py"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["workers"] == 3
    assert [u["id"] for u in out["units"]] == ["unit-1", "unit-2", "unit-3"]
    assert out["units"][0]["files"] == ["src/a.py"]
    assert out["units"][0]["event"] is None
    assert out["units"][0]["after"] == []


def test_plan_files_warns_on_units_override(capsys):
    rc = main(["plan",
               '{"kind":"implementation","independent_units":5}',
               "--files", "a.py,b.py"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["workers"] == 2
    assert any("overridden by --files" in w for w in out["warnings"])


def test_plan_files_mutually_exclusive_with_units_detail(capsys):
    spec = ('{"kind":"implementation",'
            '"units_detail":[{"id":"a","files":["x.py"]}]}')
    assert main(["plan", spec, "--files", "y.py"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_plan_files_rejects_from_triggers(tmp_path, capsys):
    assert main(["plan", "--from-triggers", str(tmp_path),
                 "--files", "a.py"]) == 2
    assert "--files" in capsys.readouterr().err


def test_plan_files_alone_uses_default_spec(capsys):
    """`plan --files` with no spec source plans against an empty spec."""
    rc = main(["plan", "--files", "src/a.py,src/b.py"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["workers"] == 2
    assert [u["files"] for u in out["units"]] == [["src/a.py"], ["src/b.py"]]


def test_plan_no_input_still_errors(capsys):
    """`plan` bare (no spec, --spec-file, --from-triggers or --files)
    stays a loud error."""
    assert main(["plan"]) == 2
    assert "task spec is required" in capsys.readouterr().err


def test_plan_files_flags_collisions(capsys):
    rc = main(["plan", '{"kind":"implementation"}',
               "--files", "src/api/,src/api/x.py"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["file_collisions"]
    assert any("collision" in w for w in out["warnings"])
