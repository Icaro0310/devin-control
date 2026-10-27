"""Offline-core guard: the core must not open sockets.

Release checklist item: "no-network core test (CI): fails if the core
opens a socket". An autouse fixture monkeypatches ``socket.socket.connect``,
``socket.socket.connect_ex`` and ``socket.create_connection`` to raise
``OfflineCoreError`` for the duration of every test in this file, then the
tests run the repo's core operations end to end. This is a guard, not a
mock: any in-process network access fails the suite.

Intentional online paths are excluded by design: none exist — the planner
emits plans and the registry records outcomes in a local JSONL file;
"nothing leaves the machine" is a stated invariant of the tool. Only
in-process sockets are blocked here.

Opt-out: mark a test ``@pytest.mark.network`` to run it without the socket
block (reserved for tests that intentionally exercise the network).

Run with: ``PYTHONPATH=src python -m pytest tests/test_offline_core.py``
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from devin_orchestrator.cli import main


class OfflineCoreError(RuntimeError):
    """Raised when core code tries to open a network connection."""


def _offline_fail(*args, **kwargs):
    raise OfflineCoreError("core opened a socket during the offline-core test")


@pytest.fixture(autouse=True)
def _block_sockets(request, monkeypatch):
    """Block all outbound sockets; opt out with ``@pytest.mark.network``."""
    if request.node.get_closest_marker("network"):
        return
    monkeypatch.setattr(socket.socket, "connect", _offline_fail)
    monkeypatch.setattr(socket.socket, "connect_ex", _offline_fail)
    monkeypatch.setattr(socket, "create_connection", _offline_fail)


def test_socket_block_is_active():
    """Sanity check: the guard itself raises on any connect attempt."""
    with pytest.raises(OfflineCoreError):
        socket.create_connection(("127.0.0.1", 1), timeout=0.01)
    with pytest.raises(OfflineCoreError):
        socket.socket().connect(("127.0.0.1", 1))


def test_plan_record_history_offline(tmp_path, capsys):
    """plan a spec, record outcomes to a tmp registry, read history."""
    rc = main(["plan", '{"kind":"implementation","independent_units":2}'])
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["workers"] == 2

    reg = tmp_path / "plans.jsonl"
    assert main(["record", '{"workers":[1,2],"mode":"parallel"}',
                 "--outcome", "success", "--registry", str(reg)]) == 0
    assert main(["record", '{"workers":[1]}',
                 "--outcome", "failed", "--registry", str(reg)]) == 0
    capsys.readouterr()

    assert main(["history", "--registry", str(reg), "--json"]) == 0
    hist = json.loads(capsys.readouterr().out)
    assert hist["total"] == 2
    assert hist["by_outcome"] == {"success": 1, "failed": 1}


def test_queue_lifecycle_offline(tmp_path, capsys):
    """submit → claim → heartbeat → done → collect → status: the whole
    queue loop is local SQLite — no socket may ever open."""
    db = tmp_path / "task-queue.db"
    assert main(["queue", "submit", '{"what":"build api"}',
                 "--batch", "b1", "--queue", str(db)]) == 0
    tid = json.loads(capsys.readouterr().out)["id"]
    assert main(["queue", "claim", "--worker", "w1",
                 "--queue", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "claimed"
    assert main(["queue", "heartbeat", tid, "--worker", "w1",
                 "--queue", str(db)]) == 0
    assert main(["queue", "done", tid, "--worker", "w1",
                 "--result", '{"ok":true}', "--queue", str(db)]) == 0
    capsys.readouterr()
    assert main(["queue", "collect", "--batch", "b1",
                 "--queue", str(db)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["complete"] and doc["by_status"] == {"succeeded": 1}
    assert main(["queue", "status", "--queue", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["held"] == 0
