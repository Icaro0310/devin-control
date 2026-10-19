#!/usr/bin/env python3
"""swapmon — SwapFile Queue collector for devin-office (Linux, stdlib only).

Reads /proc and returns the swap backlog as a queue:
  * totals from meminfo (SwapTotal/SwapFree, MemTotal/MemAvailable),
  * page-in/out rates from vmstat (pswpin/pswpout deltas between calls),
  * the queue itself: processes with pages parked in swap, ranked by
    VmSwap, flagged `shielded` when oom_score_adj <= -900 (OOM-immune).

Returns None on non-Linux hosts (no /proc) — the panel hides itself.
OFFICE_PROC_ROOT overrides the /proc root (tests).
"""
import os
import time
from pathlib import Path

PROC_ROOT = Path(os.environ.get("OFFICE_PROC_ROOT", "/proc"))
SHIELDED_ADJ = -900
QUEUE_LIMIT = 8
PAGE_KB = 4  # vmstat pswpin/pswpout are page counters (4 KiB on x86/ARM)

_last = {"t": 0.0, "pin": 0, "pout": 0}


def _kv(path: Path) -> dict:
    """Parse `Key:\tvalue unit` files (meminfo, status) → {key: int_kB}."""
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        k, _, v = line.partition(":")
        first = v.strip().split(" ")[0] if v.strip() else ""
        if first.lstrip("-").isdigit():
            out[k] = int(first)
    return out


def _vmstat(path: Path) -> tuple[int, int]:
    pin = pout = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("pswpin "):
            pin = int(line.split()[1])
        elif line.startswith("pswpout "):
            pout = int(line.split()[1])
    return pin, pout


def collect_swap(proc_root=None) -> dict | None:
    proc_root = Path(proc_root) if proc_root else PROC_ROOT
    meminfo = proc_root / "meminfo"
    # No os.name fast-path: /proc (and meminfo inside it) simply does not
    # exist on Windows — the is_file() check is the real guard, and it
    # keeps the parsing logic testable off-Linux via an injected root.
    if not meminfo.is_file():
        return None
    try:
        mi = _kv(meminfo)
        pin, pout = _vmstat(proc_root / "vmstat")
    except OSError:
        return None

    now = time.time()
    rate_in = rate_out = 0.0
    if _last["t"]:
        dt = now - _last["t"]
        if dt > 0:
            rate_in = max(0.0, (pin - _last["pin"]) * PAGE_KB / dt)
            rate_out = max(0.0, (pout - _last["pout"]) * PAGE_KB / dt)
    _last.update(t=now, pin=pin, pout=pout)

    queue = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        entries = []
    for d in entries:
        if not d.name.isdigit():
            continue
        try:
            st = _kv(d / "status")
            swap_kb = st.get("VmSwap", 0)
            if swap_kb <= 0:
                continue
            adj = int((d / "oom_score_adj").read_text().strip())
            name = (d / "comm").read_text().strip() or d.name
            queue.append({
                "pid": int(d.name),
                "name": name[:24],
                "swap_kb": swap_kb,
                "rss_kb": st.get("VmRSS", 0),
                "shielded": adj <= SHIELDED_ADJ,
            })
        except (OSError, ValueError):
            continue
    queue.sort(key=lambda p: p["swap_kb"], reverse=True)

    total = mi.get("SwapTotal", 0)
    free = mi.get("SwapFree", 0)
    return {
        "total_kb": total,
        "free_kb": free,
        "used_kb": max(0, total - free),
        "mem_total_kb": mi.get("MemTotal", 0),
        "mem_avail_kb": mi.get("MemAvailable", 0),
        "in_kbps": round(rate_in, 1),
        "out_kbps": round(rate_out, 1),
        "queue": queue[:QUEUE_LIMIT],
    }
