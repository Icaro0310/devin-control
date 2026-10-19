"""OF-1: procmap — atribuição processo→sessão (read-only)."""
import sqlite3
from pathlib import Path

from devin_office import procmap


def _mk_locks(d: Path, locks: dict) -> Path:
    d.mkdir(parents=True)
    for slug, pid in locks.items():
        (d / f"{slug}.lock").write_text(str(pid))
    return d


def _mk_sessions(path: Path, rows: list) -> Path:
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE sessions(
        id TEXT PRIMARY KEY, title TEXT, working_directory TEXT,
        model TEXT, created_at INTEGER, last_activity_at INTEGER)""")
    con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    return path


def test_read_locks_skips_garbage(tmp_path):
    d = _mk_locks(tmp_path / "l", {"good": 123, "also": 1})
    (d / "bad.lock").write_text("not-a-pid")
    assert procmap.read_locks(d) == {"good": 123, "also": 1}


def test_attribute_dead_lock(tmp_path):
    locks = _mk_locks(tmp_path / "locks", {"ghost": 99999999})
    db = _mk_sessions(tmp_path / "s.db", [("i1", "ghost", "/w", "m", 1, 2)])
    row = procmap.attribute(locks, db)["rows"][0]
    assert row["alive"] is False and row["match"] == "dead-lock"
    assert row["session"]["id"] == "i1"


def test_attribute_live_self(tmp_path):
    import os
    locks = _mk_locks(tmp_path / "locks", {"self": os.getpid()})
    db = _mk_sessions(tmp_path / "s.db", [])
    rep = procmap.attribute(locks, db)
    assert rep["rows"][0]["alive"] is True and rep["live"] == 1
