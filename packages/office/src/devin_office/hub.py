#!/usr/bin/env python3
"""office-hub — private HTTP hub for Devin Office.

Receives WorldState via POST /api/ingest from a local Windows or Linux probe
and serves index.html plus /api/state. Zero dependencies (stdlib only).

Usage: devin-office-hub [--port 8790]
       python -m devin_office.hub [--port 8790]
       python src/devin_office/hub.py [--port 8790]   (checkout, no install)
Env: OFFICE_BIND (default 127.0.0.1); non-loopback binds require OFFICE_TOKEN.
     OFFICE_CONTROL_ENABLED opts into message/spawn/kill endpoints (default off).
"""
import argparse
import ipaddress
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):
    # Loose script run from a checkout (no install): make src/ importable.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from devin_office import sessmon

ROOT = Path(__file__).resolve().parent

INDEX = (ROOT / "index.html").read_bytes()
KANBAN_INDEX = (ROOT / "kanban.html").read_bytes()
KANBAN_FILE = ROOT / "kanban.json"  # sessões fechadas no board (hub side)
TOKEN = os.environ.get("OFFICE_TOKEN", "")
BIND = os.environ.get("OFFICE_BIND", "127.0.0.1")
CONTROL_ENABLED = os.environ.get("OFFICE_CONTROL_ENABLED", "").lower() in {
    "1", "true", "yes", "on"
}
STALE_AFTER_S = 15
CMD_FILE = ROOT / "cmd_queue.json"
CMD_STALE_S = 120          # claimed sem ack há >2min → re-deliver
CMD_KEEP_S = 3600          # purga comandos terminados após 1h
MAX_TEXT = 4000
ACTIONS = {"message", "spawn", "kill"}

ECO_URL = os.environ.get("OFFICE_ECO_URL", "")
_eco_cache = {"at": 0.0, "data": None}


def validate_binding(bind: str, token: str) -> None:
    if bind == "localhost":
        loopback = True
    else:
        try:
            loopback = ipaddress.ip_address(bind).is_loopback
        except ValueError as exc:
            raise ValueError("OFFICE_BIND must be localhost or an IP address") from exc
    if not loopback and not token:
        raise ValueError("OFFICE_TOKEN is required when OFFICE_BIND is not loopback")


def eco_status() -> dict:
    """Optionally proxy a user-configured ecosystem status endpoint."""
    if not ECO_URL:
        return {"eco": None, "office": current_state()}
    now = time.time()
    if _eco_cache["data"] is not None and now - _eco_cache["at"] < 5:
        eco = _eco_cache["data"]
    else:
        try:
            import urllib.request
            with urllib.request.urlopen(ECO_URL, timeout=4) as r:
                eco = json.loads(r.read().decode())
            _eco_cache.update(at=now, data=eco)
        except Exception:
            eco = _eco_cache["data"] or {"error": "ecosystem API unreachable"}
    return {"eco": eco, "office": current_state()}


STATE_LOCK = threading.Lock()
CMD_LOCK = threading.Lock()
STATE_CACHE = {
    "ts": int(time.time()),
    "agents": [],
    "services": {},
    "events": [],
    "loading": True,
}
LAST_INGEST = {"at": 0.0}


def _load_cmds() -> list:
    try:
        return json.loads(CMD_FILE.read_text(encoding="utf-8")).get("cmds", [])
    except Exception:
        return []


def _save_cmds(cmds: list) -> None:
    tmp = CMD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cmds": cmds}, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(CMD_FILE)


def cmd_pending() -> list:
    """Devolve queued (ou claimed-expirado) e marca-os claimed atomicamente."""
    now = time.time()
    with CMD_LOCK:
        cmds = _load_cmds()
        out = []
        for c in cmds:
            if (c["status"] == "queued" or
                    (c["status"] == "claimed"
                     and now - c.get("claimed_at", 0) > CMD_STALE_S)):
                c["status"] = "claimed"
                c["claimed_at"] = now
                out.append(c)
        if out:
            _save_cmds(cmds)
    return [{"id": c["id"], "action": c["action"], "target": c["target"],
             "text": c["text"], "at": c["at"]} for c in out]


def cmd_enqueue(payload: dict):
    action = str(payload.get("action") or "")
    target = str(payload.get("target") or "")[:200]
    text = str(payload.get("text") or "")[:MAX_TEXT]
    if action not in ACTIONS:
        return None, f"unknown action {action!r}"
    if action in ("message", "kill") and not target:
        return None, "target required"
    if action in ("message", "spawn") and not text.strip():
        return None, "text required"
    cmd = {"id": f"c{int(now := time.time())}{os.urandom(2).hex()}",
           "action": action, "target": target, "text": text,
           "at": int(now), "status": "queued", "claimed_at": 0,
           "result": "", "acked_at": 0}
    with CMD_LOCK:
        cmds = _load_cmds()
        cmds.append(cmd)
        _save_cmds(cmds)
    return cmd["id"], None


def cmd_ack(payload: dict):
    cid = str(payload.get("id") or "")
    status = str(payload.get("status") or "")[:60]
    result = str(payload.get("result") or "")[:500]
    if not cid:
        return False
    now = time.time()
    with CMD_LOCK:
        cmds = _load_cmds()
        found = False
        for c in cmds:
            if c["id"] == cid:
                c["status"] = status or c["status"]
                c["result"] = result
                c["acked_at"] = now
                found = True
        # purga de terminados antigos
        terminal = {"delivered", "spawned", "interrupted", "interrupt-sent",
                    "queued-manual", "unsupported", "error"}
        cmds = [c for c in cmds
                if not (c["status"] in terminal and now - c.get("acked_at", now) > CMD_KEEP_S)]
        _save_cmds(cmds)
    return found


def cmd_acks() -> list:
    with CMD_LOCK:
        cmds = _load_cmds()
    return [{"id": c["id"], "action": c["action"], "target": c["target"],
             "status": c["status"], "result": c.get("result", ""),
             "at": c.get("acked_at") or c.get("at", 0)}
            for c in cmds if c.get("acked_at") or c["status"] != "queued"][-30:]


def current_state() -> dict:
    with STATE_LOCK:
        st = dict(STATE_CACHE)
    # kanban: os closed persistem aqui (o probe não os conhece)
    if st.get("sessions"):
        closed = sessmon.load_closed(KANBAN_FILE)
        if closed:
            st["sessions"] = sessmon.apply_closed(
                [dict(s) for s in st["sessions"]], closed)
    age = time.time() - LAST_INGEST["at"]
    st["probe"] = {
        "lastIngestAgoS": round(age, 1),
        "live": bool(LAST_INGEST["at"]) and age <= STALE_AFTER_S,
    }
    if LAST_INGEST["at"] and age > STALE_AFTER_S:
        st["stale"] = True
    return st


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path.startswith("/api/eco"):
            self._send(200, json.dumps(eco_status()).encode())
        elif self.path.startswith("/api/state"):
            self._send(200, json.dumps(current_state()).encode())
        elif self.path.startswith("/api/cmd/pending"):
            if not CONTROL_ENABLED:
                self._send(403, b'{"error":"control disabled"}')
                return
            self._send(200, json.dumps({"pending": cmd_pending()}).encode())
        elif self.path.startswith("/api/cmd/acks"):
            if not CONTROL_ENABLED:
                self._send(403, b'{"error":"control disabled"}')
                return
            self._send(200, json.dumps({"acks": cmd_acks()}).encode())
        elif self.path.startswith("/api/health"):
            self._send(200, json.dumps({"ok": True, "uptimeHint": LAST_INGEST["at"]}).encode())
        elif self.path in ("/", "/index.html"):
            self._send(200, INDEX, "text/html; charset=utf-8")
        elif self.path.split("?")[0] == "/kanban.html":
            self._send(200, KANBAN_INDEX, "text/html; charset=utf-8")
        elif self.path.startswith("/assets/"):
            self._send_static(self.path)
        else:
            self._send(404, b"404", "text/plain")

    def _send_static(self, path: str):
        rel = path.split("?", 1)[0].lstrip("/")
        target = (ROOT / rel).resolve()
        if not str(target).startswith(str(ROOT)) or not target.is_file():
            self._send(404, b"404", "text/plain")
            return
        ctype = {
            ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg",
            ".css": "text/css", ".js": "text/javascript", ".html": "text/html",
        }.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    def do_POST(self):
        if self.path.startswith("/api/kanban"):
            # marca closed/open no board — state cosmético, sem token gate
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                sid = str(payload.get("id") or "")[:80]
                action = str(payload.get("action") or "")
                if not sid or action not in ("close", "open"):
                    raise ValueError("id + action=close|open required")
                self._send(200, json.dumps(
                    sessmon.set_closed(KANBAN_FILE, sid,
                                       action == "close")).encode())
            except Exception as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode())
            return
        if not (self.path.startswith("/api/ingest")
                or self.path.startswith("/api/cmd")):
            self._send(404, b"404", "text/plain")
            return
        if TOKEN and self.headers.get("X-Office-Token") != TOKEN:
            self._send(401, b'{"error":"bad token"}')
            return
        if self.path.startswith("/api/cmd") and not CONTROL_ENABLED:
            self._send(403, b'{"error":"control disabled"}')
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode())
            return
        if self.path.startswith("/api/cmd/ack"):
            ok = cmd_ack(payload)
            self._send(200 if ok else 404,
                       json.dumps({"ok": ok}).encode())
            return
        elif self.path.startswith("/api/cmd"):
            cid, err = cmd_enqueue(payload)
            if err:
                self._send(400, json.dumps({"error": err}).encode())
            else:
                self._send(200, json.dumps({"ok": True, "id": cid}).encode())
            return
        with STATE_LOCK:
            global STATE_CACHE
            STATE_CACHE = payload
        LAST_INGEST["at"] = time.time()
        self._send(200, b'{"ok":true}')


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="devin-office-hub",
        description="Private hub for Devin Office: receives probe state, "
                    "serves the dashboard (default loopback only).")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args(argv)
    try:
        validate_binding(BIND, TOKEN)
    except ValueError as exc:
        print(f"devin-office hub: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"devin-office hub -> http://{BIND}:{args.port} "
          f"(token={'set' if TOKEN else 'loopback only'})")
    ThreadingHTTPServer((BIND, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
