"""devin-office local agent — mantém o túnel SSH e o probe vivos.

Corre com pythonw (sem janela). Se um processo morre, re-spawna.
Registado no Task Scheduler como 'DevinOffice-Agent' (ONLOGON).
Log: office/up.log
"""
import os
import subprocess
import sys
import time
from pathlib import Path
if __package__ in (None, ""):
    # Loose script run from a checkout (no install): make src/ importable so
    # the devin_office.* imports below resolve — same contract the old flat
    # sys.path.insert gave, pointed one level up at the src/ root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from devin_office.state import state_dir, spawn_cwd

ROOT = Path(__file__).resolve().parent
LOG = state_dir() / "up.log"
CREATE_NO_WINDOW = 0x08000000
SSH_HOST = os.environ.get("OFFICE_SSH_HOST", "")
OFFICE_PORT = int(os.environ.get("OFFICE_PORT", "8790"))
EXPLICIT_HUB = os.environ.get("OFFICE_HUB", "")
OFFICE_HUB = EXPLICIT_HUB or (
    f"http://127.0.0.1:{OFFICE_PORT}" if SSH_HOST else ""
)
CONTROL_ENABLED = os.environ.get("OFFICE_CONTROL_ENABLED", "").lower() in {
    "1", "true", "yes", "on"
}

JOBS = {}
if SSH_HOST and not EXPLICIT_HUB:
    JOBS["tunnel"] = [
        "ssh", "-N", "-o", "BatchMode=yes",
        "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-L", f"{OFFICE_PORT}:127.0.0.1:{OFFICE_PORT}", SSH_HOST,
    ]
if OFFICE_HUB:
    # OFFICE_HUBS (lista, vírgulas) viaja pelo ambiente — --hub só quando
    # a lista não está definida, para não achatar multi-hub num só.
    probe_cmd = [sys.executable, str(ROOT / "probe.py"), "--interval", "3"]
    if not os.environ.get("OFFICE_HUBS"):
        probe_cmd += ["--hub", OFFICE_HUB]
    JOBS["probe"] = probe_cmd
# executor ACP: o probe também o lança sob demanda; a guarda de instância
# única (named mutex no Windows, state/executor.pid no POSIX) evita duplicados.
if CONTROL_ENABLED:
    JOBS["executor"] = [sys.executable, str(ROOT / "executor.py")]
# fallback local: hub read-only em OFFICE_LOCAL_HUB_PORT — o dashboard fica
# disponível mesmo com a VM/túnel em baixo.
LOCAL_HUB_PORT = int(os.environ.get("OFFICE_LOCAL_HUB_PORT", "0"))
if LOCAL_HUB_PORT:
    JOBS["hub-local"] = [sys.executable, str(ROOT / "hub.py"), "--port", str(LOCAL_HUB_PORT)]


def log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def spawn(name: str) -> subprocess.Popen:
    p = subprocess.Popen(
        JOBS[name],
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # packages/ (checkout layout); under an install it's just a scratch
        # cwd — the spawned scripts resolve their own paths via __file__.
        cwd=str(spawn_cwd()),
    )
    log(f"START {name} pid={p.pid}")
    return p


def main() -> None:
    if not OFFICE_HUB:
        raise SystemExit("Set OFFICE_HUB or OFFICE_SSH_HOST before starting the split-mode agent")
    procs = {name: spawn(name) for name in JOBS}
    log("devin-office agent up")
    while True:
        time.sleep(5)
        for name, p in list(procs.items()):
            if p.poll() is not None:
                if name == "executor" and p.returncode == 0:
                    # rc=0 = intentional singleton-mutex exit — another
                    # instance holds the role; probe re-ensures if it dies.
                    log(f"DIED {name} rc=0 — singleton held by another instance; not respawning")
                    del procs[name]
                    continue
                log(f"DIED {name} rc={p.returncode} — respawning")
                procs[name] = spawn(name)


if __name__ == "__main__":
    main()
