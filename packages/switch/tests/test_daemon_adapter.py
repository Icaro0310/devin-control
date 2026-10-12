"""Structural checks for adapters/daemon — the drift-check scheduler.

Config artifacts, not code: assert the contract the schedulers depend on
(right ExecStart, daily-with-catch-up, durable log on Windows, consistent
task name) instead of executing the CLI.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

DAEMON = Path(__file__).resolve().parents[1] / "adapters" / "daemon"
NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"


def _read(name: str) -> str:
    return (DAEMON / name).read_text(encoding="utf-8")


class TestSystemdUnits:
    def test_service_runs_doctor_readonly(self):
        svc = _read("devin-switch-drift.service")
        assert "Type=oneshot" in svc
        assert "ExecStart=/usr/bin/env devin-switch doctor" in svc
        # notification-only hardening: writes denied outright
        assert "ProtectHome=read-only" in svc

    def test_timer_daily_with_catchup(self):
        timer = _read("devin-switch-drift.timer")
        assert "OnCalendar=" in timer
        assert "Persistent=true" in timer
        assert "WantedBy=timers.target" in timer


class TestTaskXml:
    def _root(self):
        return ET.parse(DAEMON / "devin-switch-drift.task.xml").getroot()

    def test_triggers_cover_suspend(self):
        triggers = self._root().find(f"{NS}Triggers")
        kinds = {t.tag.replace(NS, "") for t in triggers}
        assert {"LogonTrigger", "CalendarTrigger"} <= kinds

    def test_report_is_retained(self):
        args = self._root().find(f"{NS}Actions/{NS}Exec/{NS}Arguments").text
        assert "devin-switch doctor" in args
        assert "drift.log" in args  # durable log, not discarded output

    def test_task_name_matches_ps1(self):
        xml = _read("devin-switch-drift.task.xml")
        assert "<URI>\\devin-switch-drift</URI>" in xml
        for script in ("install-task.ps1", "remove-task.ps1"):
            assert "TaskName = 'devin-switch-drift'" in _read(script)
