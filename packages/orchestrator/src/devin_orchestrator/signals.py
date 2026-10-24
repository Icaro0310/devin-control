"""Environment signal extraction for the planner.

The task spec carries *task* signals supplied by the model. Machine
signals — what the host can actually bear right now — come from here.

Every signal is either a measured value or ``None``. ``None`` means
"could not determine" and never coerces to "fine": the planner treats
an unreadable signal as absent, not as clearance. Extraction is
stdlib-only and read-only; it never touches the network or writes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MEMINFO_PATH = Path("/proc/meminfo")

# Pressure when MemAvailable drops below whichever bound is larger.
# A small host (e.g. the 3.7 GB VM) trips on the absolute floor; a
# large host trips earlier via the ratio so fan-out stays polite.
RAM_PRESSURE_MIN_FREE_MB = 1536
RAM_PRESSURE_MIN_FREE_RATIO = 0.15


def _mem_kb(text: str, key: str) -> int | None:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            parts = line.split()
            try:
                return int(parts[1])
            except (IndexError, ValueError):
                return None
    return None


def read_ram(meminfo: str | None = None) -> dict[str, int | None]:
    """Parse /proc/meminfo content into total/available MB.

    ``meminfo=None`` reads the live file. A missing or unparsable file
    yields ``None`` values — never guesses a number.
    """
    if meminfo is None:
        try:
            meminfo = MEMINFO_PATH.read_text(encoding="utf-8")
        except OSError:
            return {"total_mb": None, "available_mb": None}
    total_kb = _mem_kb(meminfo, "MemTotal")
    avail_kb = _mem_kb(meminfo, "MemAvailable")
    return {
        "total_mb": total_kb // 1024 if total_kb is not None else None,
        "available_mb": (avail_kb // 1024 if avail_kb is not None else None),
    }


def ram_pressure(ram: dict[str, int | None]) -> bool | None:
    """True when free RAM is under the floor; None when unmeasurable."""
    total, avail = ram.get("total_mb"), ram.get("available_mb")
    if total is None or avail is None or total <= 0:
        return None
    return avail < max(RAM_PRESSURE_MIN_FREE_MB, total * RAM_PRESSURE_MIN_FREE_RATIO)


def collect_signals(
    env: dict[str, str] | None = None,
    *,
    meminfo: str | None = None,
) -> dict[str, Any]:
    """Snapshot the machine signals the planner honours.

    ``env`` injects the environment (tests); ``meminfo`` injects
    /proc/meminfo content. Both default to the live system.
    """
    env = env if env is not None else os.environ
    ram = read_ram(meminfo)
    return {
        "inside_worker": env.get("DEVIN_INSIDE_SUBAGENT") == "1",
        "ram_total_mb": ram["total_mb"],
        "ram_available_mb": ram["available_mb"],
        "ram_pressure": ram_pressure(ram),
    }
