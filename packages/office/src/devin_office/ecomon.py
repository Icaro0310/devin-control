#!/usr/bin/env python3
"""ecomon — ecosystem liveness collector for devin-office.

Builds the payload served at /api/eco: {host: {probes, tools, units, meta}}.

- probes: name -> HTTP status int (renderer treats 200/401/403/406 as up).
  Any HTTP response means the service is alive; None means unreachable.
- tools:  name -> bool, process liveness matched against command lines.
- units:  name -> "active"|"inactive", schedulers that are *supposed* to run
          (Windows Task Scheduler entries that are not Disabled; skipped
          entirely when Disabled — off-by-choice is not a failure).
- meta:   auxiliar counts rendered in the side panel (repos, CLIs).

Stdlib only, Windows + Linux. OFFICE_ECO_* env vars override paths/labels.
"""
import csv
import io
import json
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

IS_WIN = os.name == "nt"
HOST_LABEL = os.environ.get("OFFICE_HOST_LABEL") or (
    "windows" if IS_WIN else socket.gethostname().split(".")[0]
)

# Any HTTP status below means the endpoint answered (incl. auth-gated).
PROBE_OK = {200, 401, 403, 406}
PROBES = {
    "mcp-hub": "http://127.0.0.1:8764/healthz",
    "office-hub": "http://127.0.0.1:8791/api/state",
    "obsidian-rest": "http://127.0.0.1:27123/",
    "vm-office": "http://127.0.0.1:8790/",
    "vm-browser-mcp": "http://127.0.0.1:8765/mcp",
    "vm-ollama": "http://127.0.0.1:11435/api/tags",
}
PROBE_TIMEOUT = 0.8

# tool name -> substring(s) matched (case-insensitive) in process cmdlines.
# A tuple means "any of": memory/nlsql/obsidian/gh/ecc were absorbed into the
# unified MCP server (in-process mux), so their liveness is unified-server.py
# or, for clients that still spawn them, the standalone script.
TOOL_PATTERNS = {
    "mcp-hub": "mcp-hub.py",
    "unified-mcp": "unified-server.py",
    "memory-mcp": ("memory-server.py", "unified-server.py"),
    "nlsql-mcp": ("nlsql-server.py", "unified-server.py"),
    "obsidian-mcp": ("obsidian-bridge-server.py", "unified-server.py"),
    "gh-bridge": ("gh-bridge-server.py", "unified-server.py"),
    "ecc-bridge": ("ecc-bridge-server.py", "unified-server.py"),
    "frame-ronin": "frame_ronin",
    "poordjaevin": "poordjaevin",
    "browser-mcp": "browser-server.py",
    "slack-poll": "slack-poll",
    "devin-daemon": "devin-daemon",
    # each office component matches its script path, its console script
    # (pip/uv install) and its `python -m devin_office.<mod>` form
    "office-supervisor": ("up.pyw",),
    "office-probe": ("probe.py", "devin-office-probe", "devin_office.probe"),
    "office-executor": ("executor.py", "devin-office-executor",
                        "devin_office.executor"),
    "vm-tunnel": "devin-vm",
    "tailscale": "tailscaled",
}

_PROC_CACHE = {"at": 0.0, "cmdlines": []}
_PROC_TTL = 8.0
_TASK_CACHE = {"at": 0.0, "units": {}}
_TASK_TTL = 30.0


def _run(cmd: list, timeout: float = 4.0) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _proc_cmdlines() -> list:
    """All command lines of interesting processes (cached, one shell call)."""
    now = _now()
    if now - _PROC_CACHE["at"] < _PROC_TTL:
        return _PROC_CACHE["cmdlines"]
    lines = []
    if IS_WIN:
        names = ("pythonw.exe", "python.exe", "node.exe", "powershell.exe",
                 "pwsh.exe", "ssh.exe", "tailscaled.exe")
        filt = " OR ".join(f"Name='{n}'" for n in names)
        raw = _run([
            "powershell", "-NoProfile", "-Command",
            (f"Get-CimInstance Win32_Process -Filter \"{filt}\" "
             "| Select-Object -ExpandProperty CommandLine"),
        ], timeout=6.0)
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        # tailscaled tem cmdline vazia como servico — presenca do nome basta
        if _run(["powershell", "-NoProfile", "-Command",
                 ("(Get-Process tailscaled -ErrorAction SilentlyContinue) "
                  "-ne $null")], timeout=3.0).strip().lower() == "true":
            lines.append("tailscaled")
    else:
        raw = _run(["ps", "-eo", "args="])
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
    _PROC_CACHE.update(at=now, cmdlines=lines)
    return lines


def _now() -> float:
    import time
    return time.time()


def _probe(url: str):
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code  # resposta HTTP = servico vivo
    except Exception:
        return None


def _units() -> dict:
    """Scheduled jobs que devem correr. Disabled = intencional -> omite."""
    now = _now()
    if now - _TASK_CACHE["at"] < _TASK_TTL:
        return _TASK_CACHE["units"]
    units = {}
    if IS_WIN:
        raw = _run(["schtasks", "/query", "/fo", "csv", "/nh"], timeout=8.0)
        for row in csv.reader(io.StringIO(raw)):
            if len(row) < 3:
                continue
            name = row[0].strip('"').lstrip("\\")
            if not name.lower().startswith("devin"):
                continue
            status = row[-1].strip('"')
            if status == "Disabled":
                continue
            units[name] = "active" if status in ("Ready", "Running") else "inactive"
    else:
        raw = _run(["systemctl", "--user", "list-units", "--type=timer",
                    "--no-legend", "--no-pager"], timeout=4.0)
        for l in raw.splitlines():
            parts = l.split()
            if len(parts) >= 4 and "devin" in parts[0].lower():
                units[parts[0]] = "active" if parts[3] == "active" else "inactive"
    _TASK_CACHE.update(at=now, units=units)
    return units


def _registry_names(eco_dir: Path):
    """Repo names from the powerups registry, or None when unavailable.

    Clones whose name left the registry (absorbed/renamed/archived repos)
    are counted separately as `retired` instead of inflating `repos`.
    """
    reg_path = os.environ.get("OFFICE_REGISTRY_JSON")
    p = Path(reg_path) if reg_path else eco_dir / "devin-powerups" / "registry.json"
    try:
        repos = json.loads(p.read_text(encoding="utf-8"))["repositories"]
        return {r["name"] for r in repos}
    except Exception:
        return None


def _meta() -> dict:
    """Contagens de suporte: repos clonados, CLIs pipx, db de sessoes."""
    meta = {}
    eco = os.environ.get("OFFICE_ECOSYSTEM_DIR")
    cands = [Path(eco)] if eco else [
        Path.home() / "Desktop" / "feat" / "devin-ecosystem",
        Path.home() / "devin" / "devin-ecosystem",
        Path.home() / "devin-ecosystem",
    ]
    for d in cands:
        if d.is_dir():
            clones = {p.name for p in d.iterdir() if (p / ".git").exists()}
            registry = _registry_names(d)
            if registry is None:
                meta["repos"] = len(clones)
            else:
                meta["repos"] = len(clones & registry)
                retired = clones - registry
                if retired:
                    meta["retired"] = len(retired)
            break
    bin_dir = Path.home() / ".local" / "bin"
    if bin_dir.is_dir():
        suffix = ".exe" if IS_WIN else ""
        meta["clis"] = sum(
            1 for p in bin_dir.iterdir()
            if p.name.startswith(("devin-", "poordjaevin"))
            and (not suffix or p.suffix == suffix)
        )
    return meta


def collect_eco() -> dict:
    now_lines = _proc_cmdlines()
    low = [l.lower() for l in now_lines]
    tools = {
        name: any(
            any(p.lower() in l for l in low)
            for p in (pat if isinstance(pat, tuple) else (pat,))
        )
        for name, pat in TOOL_PATTERNS.items()
    }
    return {
        HOST_LABEL: {
            "probes": {name: _probe(url) for name, url in PROBES.items()},
            "tools": tools,
            "units": _units(),
            "meta": _meta(),
        }
    }


if __name__ == "__main__":
    import time
    t = time.time()
    print(json.dumps(collect_eco(), indent=2))
    print(f"collect took {time.time()-t:.2f}s", file=__import__("sys").stderr)
