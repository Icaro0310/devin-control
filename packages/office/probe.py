#!/usr/bin/env python3
"""office-probe — probe magro do devin-office, corre na máquina local.

Recolhe o WorldState (sessions.db, jev_log.db, heartbeat) e faz POST para
o hub na VM quando o estado muda, mais keepalive a cada KEEPALIVE s para o
hub medir lastIngestAgoS como sinal de vida. Pensado para ~20 MB de RAM.

Também faz a ponte de comandos (control path):
  GET  /api/cmd/pending   → escreve office/cmd_inbox/<id>.json p/ executor.py
  outbox/<id>.json        → POST /api/cmd/ack {id,status,result}

O executor (office/executor.py) fala ACP com `devin acp` e é quem executa
message/spawn/kill a sério. Se não estiver vivo, o probe lança-o detached —
up.pyw também o supervisiona.

Uso: python office/probe.py [--hub http://<your-vm>:8790] [--interval 3]
Env: OFFICE_HUB — URL do hub (default http://localhost:8790)
     OFFICE_TOKEN — enviado como X-Office-Token se definido.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daemon import collect_state
from executor import (
    INBOX,
    OUTBOX,
    ensure_dirs,
    singleton_alive,
)

ROOT = Path(__file__).resolve().parent
EXECUTOR = ROOT / "executor.py"
CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008

HUBS = [
    h.rstrip("/") for h in
    os.environ.get("OFFICE_HUBS",
                   os.environ.get("OFFICE_HUB", "http://localhost:8790")
                   ).split(",") if h.strip()
]
INTERVAL = float(os.environ.get("OFFICE_INTERVAL", "3"))
KEEPALIVE = float(os.environ.get("OFFICE_KEEPALIVE", "60"))
TOKEN = os.environ.get("OFFICE_TOKEN", "")
CONTROL_ENABLED = os.environ.get("OFFICE_CONTROL_ENABLED", "").lower() in {
    "1", "true", "yes", "on"
}

def executor_spawn_kwargs(platform: str | None = None) -> dict:
    current = os.name if platform is None else platform
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(ROOT),
        "close_fds": True,
    }
    if current == "nt" or current.startswith("win"):
        options["creationflags"] = CREATE_NO_WINDOW | DETACHED
    else:
        options["start_new_session"] = True
    return options


def http(hub: str, path: str, body=None, timeout=5):
    req = urllib.request.Request(
        f"{hub}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 "X-Office-Token": TOKEN},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def push(hub: str, state: dict) -> bool:
    try:
        code, _ = http(hub, "/api/ingest", state, timeout=4)
        return code == 200
    except Exception:
        return False


def ensure_executor() -> None:
    try:
        if singleton_alive():
            return
    except Exception:
        pass
    try:
        subprocess.Popen(
            [sys.executable, str(EXECUTOR)], **executor_spawn_kwargs())
        print("probe: executor spawned", flush=True)
    except Exception as exc:
        print(f"probe: executor spawn failed: {exc}", flush=True)


def poll_commands(acked: dict, last_ensure: list) -> None:
    """claimed cmds → inbox; outbox finals → ack ao hub."""
    if time.time() - last_ensure[0] > 30:
        last_ensure[0] = time.time()
        ensure_executor()
    for hub in HUBS:
        try:
            _, data = http(hub, "/api/cmd/pending", timeout=4)
        except Exception:
            continue
        for cmd in data.get("pending", []):
            cid = cmd.get("id")
            if not cid:
                continue
            try:
                (INBOX / f"{cid}.json").write_text(
                    json.dumps(cmd, ensure_ascii=False), encoding="utf-8")
                http(hub, "/api/cmd/ack", {"id": cid, "status": "dispatched",
                                           "result": "queued for local executor"})
                acked[cid] = "dispatched"
                ensure_executor()
            except Exception:
                pass
    # resultados do executor → acks
    terminal = ("delivered", "spawned", "interrupted", "interrupt-sent",
                "queued-manual", "unsupported", "error")
    for f in OUTBOX.glob("*.json"):
        cid = f.stem
        if cid.startswith("_"):
            continue
        try:
            res = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        status = res.get("status", "")
        if acked.get(cid) == status:
            continue
        for hub in HUBS:
            try:
                http(hub, "/api/cmd/ack", {"id": cid, "status": status,
                                           "result": res.get("detail", "")})
                done = True
            except urllib.error.HTTPError as exc:
                done = exc.code == 404   # id desconhecido no hub → desiste
            except Exception:
                done = False
            if done:
                break
        if done:
            acked[cid] = status
            if status in terminal:
                f.unlink(missing_ok=True)


def main() -> None:
    last_hash = {h: "" for h in HUBS}
    last_push = {h: 0.0 for h in HUBS}
    failures = 0
    acked = {}
    last_ensure = [0.0]
    print(f"devin-office probe -> {HUBS} every {INTERVAL}s")
    if CONTROL_ENABLED:
        ensure_dirs()
        ensure_executor()
    while True:
        try:
            state = collect_state()
            blob = json.dumps(state, sort_keys=True)
            h = hashlib.sha1(blob.encode()).hexdigest()
            now = time.time()
            sent = 0
            for hub in HUBS:
                # skip só se o keepalive estiver dentro do prazo — força um
                # POST ≥1x/KEEPALIVE mesmo sem mudança (lastIngestAgoS do hub)
                if h == last_hash[hub] and now - last_push[hub] < KEEPALIVE:
                    sent += 1
                    continue
                if push(hub, state):
                    last_hash[hub] = h
                    last_push[hub] = now
                    sent += 1
            if sent:
                failures = 0
            else:
                failures += 1
                if failures in (1, 10, 60):
                    print(f"probe: all hubs unreachable ({failures} failures)", flush=True)
            if CONTROL_ENABLED:
                poll_commands(acked, last_ensure)
        except Exception as exc:
            print(f"probe: collect error: {exc}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--hub" in args:
        HUBS = [args[args.index("--hub") + 1].rstrip("/")]
    if "--interval" in args:
        INTERVAL = float(args[args.index("--interval") + 1])
    main()
