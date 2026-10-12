"""Structural checks for adapters/daemon — the resident dashboard unit.

The unit is config, not code: assert the contract a resident loopback
server needs (restart policy, writable session_locks + checkout for the
flock probe and kanban.json, loopback-only network, at-logon task with
no execution-time cap).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

DAEMON = Path(__file__).resolve().parents[1] / "adapters" / "daemon"
NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"


def _read(name: str) -> str:
    return (DAEMON / name).read_text(encoding="utf-8")


class TestService:
    def test_resident_restart_policy(self):
        svc = _read("devin-office.service")
        assert "Type=simple" in svc
        assert "Restart=on-failure" in svc
        assert "WantedBy=default.target" in svc
        assert "daemon.py --port" in svc

    def test_writable_paths_cover_kanban_and_locks(self):
        svc = _read("devin-office.service")
        rw = next(l for l in svc.splitlines() if l.startswith("ReadWritePaths="))
        assert "session_locks" in rw
        assert "packages/office" in rw  # kanban.json + .tmp live in the checkout

    def test_loopback_confinement(self):
        svc = _read("devin-office.service")
        assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in svc
        assert "IPAddressAllow=localhost" in svc
        assert "IPAddressDeny=any" in svc


class TestTaskXml:
    def _root(self):
        return ET.parse(DAEMON / "devin-office.task.xml").getroot()

    def test_logon_trigger_no_time_cap(self):
        root = self._root()
        assert root.find(f"{NS}Triggers/{NS}LogonTrigger") is not None
        limit = root.find(f"{NS}Settings/{NS}ExecutionTimeLimit").text
        assert limit == "PT0S"  # a resident server must not be killed at 72h

    def test_checkout_placeholder_patched_by_installer(self):
        args = self._root().find(f"{NS}Actions/{NS}Exec/{NS}Arguments").text
        assert "__OFFICE_CHECKOUT__" in args
        assert "daemon.py" in args
        assert "__OFFICE_CHECKOUT__" in _read("install-task.ps1")

    def test_task_name_matches_ps1(self):
        xml = _read("devin-office.task.xml")
        assert "<URI>\\devin-office</URI>" in xml
        for script in ("install-task.ps1", "remove-task.ps1"):
            assert "TaskName = 'devin-office'" in _read(script)
