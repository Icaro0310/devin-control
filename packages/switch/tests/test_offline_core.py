"""Offline-core guard: the core must not open sockets.

Release checklist item: "no-network core test (CI): fails if the core
opens a socket". An autouse fixture monkeypatches ``socket.socket.connect``,
``socket.socket.connect_ex`` and ``socket.create_connection`` to raise
``OfflineCoreError`` for the duration of every test in this file, then the
tests run the repo's core operations end to end. This is a guard, not a
mock: any in-process network access fails the suite.

Intentional online paths are excluded by design: none exist in the core —
list/show/diff/doctor/use/rollback only read and rewrite local config
files. Only in-process sockets are blocked here.

Opt-out: mark a test ``@pytest.mark.network`` to run it without the socket
block (reserved for tests that intentionally exercise the network).

Run with: ``PYTHONPATH=src:. python -m pytest tests/test_offline_core.py``
(the ``.`` lets ``tests.conftest`` import as a package module).
"""

from __future__ import annotations

import socket

import pytest
from devin_switch import cli

from tests.conftest import cli_argv


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


def test_list_diff_doctor_offline(roots, capsys):
    """Read-only commands over the conftest fake roots — all offline."""
    assert cli.main(cli_argv(roots, "list")) == 0
    out = capsys.readouterr().out
    assert "work" in out and "home" in out

    assert cli.main(cli_argv(roots, "diff", "work", "home")) == 0
    out = capsys.readouterr().out
    assert "diff work -> home" in out

    assert cli.main(cli_argv(roots, "doctor")) == 0

    assert cli.main(cli_argv(roots, "verify-env")) == 0
    assert "PASS" in capsys.readouterr().out
