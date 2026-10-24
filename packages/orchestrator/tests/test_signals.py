"""Signal extraction and planner consumption — no live host reads."""

import json

from devin_orchestrator.cli import main as cli_main
from devin_orchestrator.planner import plan_task
from devin_orchestrator.signals import collect_signals, ram_pressure, read_ram

MEMINFO_ROOMY = (
    "MemTotal:       32768000 kB\n"
    "MemFree:         8000000 kB\n"
    "MemAvailable:   20000000 kB\n"
    "Buffers:          500000 kB\n"
)
MEMINFO_TIGHT = (
    "MemTotal:        3800000 kB\n"
    "MemFree:          200000 kB\n"
    "MemAvailable:     400000 kB\n"  # 390 MB < 1536 MB floor
)
MEMINFO_GARBAGE = "not a real meminfo\n"


def test_read_ram_parses_meminfo():
    ram = read_ram(MEMINFO_ROOMY)
    assert ram["total_mb"] == 32000
    assert ram["available_mb"] == 19531


def test_read_ram_garbage_returns_nones():
    assert read_ram(MEMINFO_GARBAGE) == {"total_mb": None, "available_mb": None}


def test_ram_pressure_true_when_constrained():
    assert ram_pressure(read_ram(MEMINFO_TIGHT)) is True


def test_ram_pressure_false_with_headroom():
    assert ram_pressure(read_ram(MEMINFO_ROOMY)) is False


def test_ram_pressure_none_when_unmeasurable():
    assert ram_pressure({"total_mb": None, "available_mb": None}) is None


def test_collect_signals_injects():
    s = collect_signals(
        env={"DEVIN_INSIDE_SUBAGENT": "1"}, meminfo=MEMINFO_TIGHT)
    assert s["inside_worker"] is True
    assert s["ram_pressure"] is True
    assert s["ram_available_mb"] == 390


def test_pressure_caps_fanout_at_one():
    plan = plan_task(
        {"kind": "implementation", "independent_units": 3},
        signals={"ram_pressure": True, "ram_available_mb": 390},
    )
    assert plan.workers == 1
    assert any("ram_pressure" in w for w in plan.warnings)


def test_pressure_does_not_promote_inline_to_fanout():
    plan = plan_task(
        {"kind": "fix", "estimated_scope": "trivial"},
        signals={"ram_pressure": True, "ram_available_mb": 100},
    )
    assert plan.workers == 0


def test_unmeasurable_ram_never_caps():
    plan = plan_task(
        {"kind": "implementation", "independent_units": 3},
        signals={"ram_pressure": None, "ram_available_mb": None},
    )
    assert plan.workers == 3
    assert not any("ram_pressure" in w for w in plan.warnings)


def test_plan_carries_the_signals_it_saw():
    plan = plan_task(
        {"kind": "implementation", "independent_units": 2},
        signals={"ram_pressure": False, "ram_available_mb": 9000},
    )
    assert plan.to_dict()["signals"]["ram_pressure"] is False


def test_cli_signals_prints_json(capsys):
    assert cli_main(["signals"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "ram_pressure" in out
    assert "inside_worker" in out
