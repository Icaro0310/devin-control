#!/usr/bin/env python3
"""procmap — OF-1: atribuição processo → sessão Devin (read-only).

Lê ``session_locks/*.lock`` (cada um guarda o PID dono da sessão), percorre
a tabela de processos (``/proc`` no Linux; ``tasklist``/``ps`` noutras
plataformas — best-effort) e resolve slugs para sessões no ``sessions.db``
(read-only). Nada é escrito, sinalizado ou morto.

Uso: python procmap.py [--dead-only] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):
    # Loose script run from a checkout (no install): make src/ importable.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from devin_office.daemon import SESSION_LOCKS, SESSIONS_DB


@dataclass
class Proc:
    pid: int
    ppid: int
    cmdline: str
    rss_kb: int = 0
    children: list = field(default_factory=list)


def read_locks(locks_dir: Path) -> dict:
    """``<slug>.lock`` → PID; conteúdo inválido é ignorado."""
    out = {}
    if not locks_dir.is_dir():
        return out
    for f in sorted(locks_dir.glob("*.lock")):
        try:
            pid = int(f.read_text().strip())
        except (ValueError, OSError):
            continue
        if pid > 0:
            out[f.stem] = pid
    return out


def _linux_procs() -> dict:
    procs = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text()
            tail = stat[stat.rindex(")") + 2:]
            ppid = int(tail.split()[1])
            cmdline = (entry / "cmdline").read_bytes().replace(
                b"\x00", b" ").decode(errors="replace").strip()
            rss = 0
            try:
                for line in (entry / "status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1])
                        break
            except OSError:
                pass
            procs[pid] = Proc(
                pid=pid, ppid=ppid,
                cmdline=cmdline or stat.split("(")[1].split(")")[0],
                rss_kb=rss)
        except (OSError, IndexError, ValueError):
            continue
    for p in procs.values():
        if p.ppid in procs:
            procs[p.ppid].children.append(p.pid)
    return procs


def _fallback_procs() -> dict:
    """Windows/macOS — nomes + RSS via tasklist/ps, menos detalhe."""
    procs = {}
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=10, check=False).stdout
            import csv
            import io
            for row in csv.reader(io.StringIO(out)):
                if len(row) >= 2 and row[1].isdigit():
                    procs[int(row[1])] = Proc(pid=int(row[1]), ppid=0,
                                              cmdline=row[0])
        else:
            out = subprocess.run(
                ["ps", "-eo", "pid,ppid,rss,comm"],
                capture_output=True, text=True, timeout=10, check=False).stdout
            for line in out.splitlines()[1:]:
                parts = line.split(None, 3)
                if len(parts) == 4 and parts[0].isdigit():
                    procs[int(parts[0])] = Proc(
                        pid=int(parts[0]), ppid=int(parts[1]),
                        rss_kb=int(parts[2]) if parts[2].isdigit() else 0,
                        cmdline=parts[3])
    except (OSError, subprocess.TimeoutExpired):
        pass
    for p in procs.values():
        if p.ppid in procs:
            procs[p.ppid].children.append(p.pid)
    return procs


def list_procs() -> dict:
    return _linux_procs() if Path("/proc").is_dir() else _fallback_procs()


def _descendants(procs: dict, pid: int, depth: int = 4) -> list:
    out, seen, frontier = [], set(), [pid]
    while frontier and depth > 0:
        nxt = []
        for p in frontier:
            for c in procs.get(p, Proc(p, 0, "")).children:
                if c not in seen and c in procs:
                    seen.add(c)
                    out.append(procs[c])
                    nxt.append(c)
        frontier, depth = nxt, depth - 1
    return out


def _session_index(sessions_db: Path) -> dict:
    if not sessions_db.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{sessions_db}?mode=ro", uri=True,
                              timeout=0.75)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, title, working_directory, model, created_at,"
            " last_activity_at FROM sessions").fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    idx = {}
    for r in rows:
        d = dict(r)
        idx[r["id"]] = d
        if r["title"]:
            idx[r["title"]] = d
    return idx


def attribute(locks_dir: Path, sessions_db: Path) -> dict:
    locks = read_locks(locks_dir)
    procs = list_procs()
    sessions = _session_index(sessions_db)
    rows = []
    for slug, pid in sorted(locks.items()):
        proc = procs.get(pid)
        children = _descendants(procs, pid) if proc else []
        rows.append({
            "slug": slug,
            "pid": pid,
            "alive": proc is not None,
            "cmdline": proc.cmdline[:160] if proc else None,
            "rss_kb": proc.rss_kb if proc else 0,
            "children": [
                {"pid": c.pid, "cmdline": c.cmdline[:120], "rss_kb": c.rss_kb}
                for c in children
            ],
            "session": sessions.get(slug),
            "match": "lock-pid" if proc else "dead-lock",
        })
    return {
        "locks": len(locks),
        "live": sum(1 for r in rows if r["alive"]),
        "dead": sum(1 for r in rows if not r["alive"]),
        "rows": rows,
        "note": "read-only — locks, /proc and sessions.db were only read",
    }


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} MB" if n >= 1024 else f"{n} KB"


def main() -> int:
    p = argparse.ArgumentParser(
        prog="procmap",
        description="atribui processos em execução a sessões Devin "
        "(read-only)")
    p.add_argument("--locks-dir", type=Path, default=SESSION_LOCKS)
    p.add_argument("--sessions-db", type=Path, default=SESSIONS_DB)
    p.add_argument("--dead-only", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    report = attribute(args.locks_dir, args.sessions_db)
    if args.dead_only:
        report["rows"] = [r for r in report["rows"] if not r["alive"]]
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(f"{report['live']} live / {report['dead']} dead lock(s) "
          f"of {report['locks']} — read-only")
    for r in report["rows"]:
        mark = "LIVE" if r["alive"] else "dead"
        sess = r["session"] or {}
        title = (sess.get("title") or sess.get("id") or "?")[:40]
        print(f"  [{mark}] {r['slug']:<24} pid={r['pid']} "
              f"rss={_kb(r['rss_kb'])}  session={title}")
        for c in r["children"]:
            print(f"       └─ pid={c['pid']} rss={_kb(c['rss_kb'])} "
                  f"{c['cmdline'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
