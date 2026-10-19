#!/usr/bin/env python3
"""office-daemon — standalone local Devin Office server.

Polls sessions.db read-only and serves WorldState at /api/state plus index.html.
Zero dependencies (stdlib only).

Usage: devin-office [--port 8788]
       python -m devin_office.daemon [--port 8788]
       python src/devin_office/daemon.py [--port 8788]   (checkout, no install)
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):
    # Loose script run from a checkout (no install): make src/ importable so
    # the devin_office.* imports below resolve — same contract the old flat
    # sys.path.insert gave, pointed one level up at the src/ root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from devin_office import sessmon
from devin_office.state import state_dir
from devin_office.ecomon import collect_eco
from devin_office.swapmon import collect_swap

ROOT = Path(__file__).resolve().parent

# Devin stores: Windows → %APPDATA%\devin (data) + %APPDATA%\Devin (config);
# Linux → ~/.local/share/devin + ~/.config/Devin. Override com env vars.
def _devin_dirs() -> tuple[Path, Path]:
    data = os.environ.get("OFFICE_DATA_DIR")
    conf = os.environ.get("OFFICE_CONF_DIR")
    appdata = os.environ.get("APPDATA")
    if appdata:
        d_default = Path(appdata) / "devin"
        c_default = Path(appdata) / "Devin"
    else:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        d_default = data_home / "devin"
        c_default = config_home / "Devin"
    return Path(data) if data else d_default, Path(conf) if conf else c_default


DATA_DIR, CONF_DIR = _devin_dirs()
SESSIONS_DB = DATA_DIR / "cli" / "sessions.db"
SESSION_LOCKS = DATA_DIR / "cli" / "session_locks"
ACP_MSG_DIR = CONF_DIR / "User" / "acp-messages"
VSCDB = CONF_DIR / "User" / "globalStorage" / "state.vscdb"
JEV_DB = Path(os.environ["OFFICE_JEV_DB"]).expanduser() if os.environ.get("OFFICE_JEV_DB") else None
HEARTBEAT_FILE = (
    Path(os.environ["OFFICE_HEARTBEAT_FILE"]).expanduser()
    if os.environ.get("OFFICE_HEARTBEAT_FILE") else None
)
ACTIVE_WINDOW_S = 15 * 60  # sessão "viva" se teve atividade nos últimos 15 min
KANBAN_FILE = state_dir() / "kanban.json"  # sessões fechadas no board (standalone)
SUBAGENT_TTL_S = 30 * 60
WAIT_GRACE_S = 20          # turno "terminado" se última msg é texto puro há >20s
MAX_FILES = 6

AGENT_ID_RE = re.compile(r"agent_id=([0-9a-f\-]+)")
COMPLETION_RE = re.compile(r"agent_id=([0-9a-f\-]+) completed")
FILE_RE = re.compile(r"^([A-Za-z]:[\\/]|\.{0,2}[\\/]|/|\w+[/\\])")


def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.75)


def jload(s):
    try:
        return json.loads(s)
    except Exception:
        return None


def session_locked(sid: str) -> bool:
    """Lock file é mantido (OS-level) enquanto a sessão está aberta num
    processo devin (GUI ou CLI). POSIX usa flock (locks advisory não falham
    no open); Windows falha o open enquanto o holder mantém o lock."""
    return sessmon.lock_held(SESSION_LOCKS, sid)


def acp_map() -> dict:
    """session_id -> acp-messages db path, via state.vscdb do Devin Desktop."""
    out = {}
    if not VSCDB.exists():
        return out
    try:
        db = ro(VSCDB)
        prefix = "windsurf.acp.messageStore.session.acp/devin-cli/"
        for k, v in db.execute(
            "select key, value from ItemTable where key like ?", (prefix + "%",)
        ):
            sid = k[len(prefix):]
            uuid = (jload(v) or {}).get("uuid")
            if sid and uuid:
                f = ACP_MSG_DIR / f"{uuid}.db"
                if f.exists():
                    out[sid] = f
        db.close()
    except Exception:
        pass
    return out


def acp_last_tool_status(path: Path) -> str | None:
    """Status da última tool_call na acp db de uma sessão GUI
    (in_progress/pending/completed/...). Autoritativo para o Desktop:
    a db não escreve enquanto uma tool corre, por isso o mtime não chega."""
    try:
        db = ro(path)
        row = db.execute(
            "select payload from messages where kind='tool_call' "
            "order by position desc limit 1"
        ).fetchone()
        db.close()
        if row:
            return ((jload(row[0]) or {}).get("content") or {}).get("status")
    except Exception:
        pass
    return None


def acp_subagents(path: Path) -> list:
    """Lê mensagens kind='subagent' da ACP db de uma sessão GUI.
    Cada uma tem status real + childMessages (tool calls do worker)."""
    subs = []
    try:
        db = ro(path)
        for (pl,) in db.execute(
            "select payload from messages where kind='subagent' "
            "order by position asc limit 40"
        ):
            d = jload(pl) or {}
            kids = d.get("childMessages") or []
            tools = [m.get("content") or {} for m in kids
                     if m.get("kind") == "tool_call"]
            last = tools[-1] if tools else {}
            files = []
            for c in tools:
                for p in extract_files(c.get("rawInput") or {},
                                       c.get("locations")):
                    if p not in files:
                        files.append(p)
            subs.append({
                "agentId": d.get("agentId"),
                "title": d.get("title") or "",
                "profile": d.get("profile") or "?",
                "state": ("running" if d.get("status") == "running"
                          else "completed"),
                "status": d.get("status"),
                "lastTool": last.get("title") or None,
                "lastToolStatus": last.get("status"),
                "files": files[-MAX_FILES:],
            })
        db.close()
    except Exception:
        pass
    return subs


PATH_KEYS = ("file_path", "target_file", "path", "filename")


def extract_files(raw: dict, locations=None) -> list:
    out = []
    for k in PATH_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and FILE_RE.match(v):
            out.append(v)
    for loc in locations or []:
        if isinstance(loc, dict) and loc.get("path"):
            out.append(str(loc["path"]))
    return out


def short_args(raw: dict) -> str:
    if not raw:
        return ""
    if raw.get("command"):
        return str(raw["command"]).splitlines()[0][:70]
    for k in ("file_path", "path", "pattern", "query", "url", "agent_id"):
        if raw.get(k):
            return str(raw[k])[:70]
    if raw.get("server_name"):
        return f"{raw['server_name']}/{raw.get('tool_name', '')}"[:70]
    for v in raw.values():
        if isinstance(v, str) and v:
            return v[:70]
    return ""


def inference_name(tc_json: dict) -> str:
    meta = tc_json.get("_meta") or {}
    return meta.get("cognition.ai/inferenceToolName") or tc_json.get("kind") or "?"


def display_event_title(title: str) -> str:
    return re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\)+", lambda _match: "…\\", title)


def collect_state() -> dict:
    now = int(time.time())
    state = {
        "ts": now,
        "agents": [],
        "services": {},
        "events": [],
    }
    if not SESSIONS_DB.exists():
        return state | {"error": f"sessions.db not found: {SESSIONS_DB}"}

    db = ro(SESSIONS_DB)
    sessions = db.execute(
        "select id, title, model, agent_mode, last_activity_at, working_directory, "
        "created_at, hidden from sessions where coalesce(hidden, 0)=0 "
        "and last_activity_at>=? order by last_activity_at desc limit 20",
        (now - ACTIVE_WINDOW_S,),
    ).fetchall()
    active_ids = [row[0] for row in sessions]
    per_session_tools = {}
    events = []
    completed_by_session = {}
    last_nodes = {}
    if active_ids:
        placeholders = ",".join("?" for _ in active_ids)
        tool_rows = db.execute(
            "select session_id, tool_call_json, tool_call_update_json, rowid "
            f"from tool_call_state where session_id in ({placeholders}) "
            "order by rowid desc limit 800",
            active_ids,
        ).fetchall()
        for sid, tj, tu, rid in reversed(tool_rows):
            d = jload(tj) or {}
            u = jload(tu) or {}
            entry = {
                "name": inference_name(d),
                "title": (d.get("title") or "")[:90],
                "kind": d.get("kind"),
                "status": u.get("status"),
                "raw": d.get("rawInput") or {},
                "locations": d.get("locations"),
                "rid": rid,
            }
            per_session_tools.setdefault(sid, []).append(entry)
            events.append((rid, sid, entry))

        # último nó de mensagem por sessão → detecta "awaiting input"
        for sid in active_ids:
            row = db.execute(
                "select chat_message from message_nodes where session_id=? "
                "order by row_id desc limit 1", (sid,)).fetchone()
            d = jload(row[0]) if row else None
            if d:
                last_nodes[sid] = {
                    "role": d.get("role"),
                    "has_tools": bool(d.get("tool_calls")),
                }

        # completions de subagentes (role=system na chain do pai)
        for sid, cm in db.execute(
            "select session_id, chat_message from message_nodes "
            f"where session_id in ({placeholders}) and chat_message "
            "like '%subagent_completion_notification%' "
            "order by rowid desc limit 300",
            active_ids,
        ):
            completed_by_session[sid] = completed_by_session.get(sid, 0) + len(
                COMPLETION_RE.findall(cm or "")
            )

    acp_by_sid = acp_map()

    # sessions ativas → NPCs
    for sid, title, model, mode, last_act, cwd, created, hidden in sessions:
        if hidden or now - (last_act or 0) > ACTIVE_WINDOW_S:
            continue
        tools = per_session_tools.get(sid, [])
        last_tool = tools[-1] if tools else None
        subs = []
        n_completed = completed_by_session.get(sid, 0)
        n_spawned = 0
        acp_subs = acp_subagents(acp_by_sid[sid]) if sid in acp_by_sid else []
        used_acp = set()
        for t in tools:
            if t["name"] == "run_subagent":
                n_spawned += 1
                # heurística smoke: completions ≥ spawns desta sessão → done
                state_sub = ("completed" if n_completed >= n_spawned
                             else "running")
                sub = {
                    "profile": t["raw"].get("profile", "?"),
                    "title": t["raw"].get("title", "subagent"),
                    "state": state_sub,
                    "rid": t["rid"],
                }
                # enriquece com a ACP db do GUI (status real + child tools)
                for i, a in enumerate(acp_subs):
                    if i in used_acp:
                        continue
                    if a["title"] and a["title"] == sub["title"]:
                        sub["state"] = a["state"]
                        sub["lastTool"] = a.get("lastTool")
                        sub["lastToolStatus"] = a.get("lastToolStatus")
                        sub["files"] = a.get("files", [])
                        used_acp.add(i)
                        break
                subs.append(sub)

        # files recentemente tocados (dedupe, mais recente por último)
        files = []
        for t in tools:
            for p in extract_files(t["raw"], t.get("locations")):
                if p not in files:
                    files.append(p)
        files = files[-MAX_FILES:]

        # waiting: pedido de permissão pendente OU turno terminado à espera
        # do utilizador (última msg = texto puro do assistant, sessão aberta).
        node = last_nodes.get(sid) or {}
        waiting = bool(
            (last_tool and last_tool["status"] == "pending")
            or (session_locked(sid)
                and node.get("role") == "assistant"
                and not node.get("has_tools")
                and now - (last_act or 0) > WAIT_GRACE_S)
        )

        agent = {
            "id": sid,
            "kind": "agent",
            "name": "Devin",
            "title": (title or "")[:80],
            "model": model or "?",
            "project": Path(cwd).name if cwd else "",
            "state": ("failed" if last_tool and last_tool["status"] == "failed"
                      else "idle" if now - (last_act or 0) > 120 else "working"),
            "currentTool": (last_tool["name"] if last_tool else None),
            "currentToolTitle": (last_tool["title"] if last_tool else ""),
            "lastToolArgs": (short_args(last_tool["raw"]) if last_tool else ""),
            "files": files,
            "waiting": waiting,
            "locked": session_locked(sid),
            "toolCount": len(tools),
            "lastActivity": last_act,
            "subagents": subs,
        }
        state["agents"].append(agent)

        # MCP usage → service NPCs
        for t in tools:
            srv = t["raw"].get("server_name")
            if srv:
                svc = state["services"].setdefault(srv, {
                    "id": f"mcp:{srv}", "kind": "mcp", "name": srv,
                    "calls": 0, "lastTool": None, "state": "idle",
                })
                svc["calls"] += 1
                svc["lastTool"] = t["raw"].get("tool_name") or t["name"]
                svc["state"] = "active" if t["rid"] == (tools[-1]["rid"] if tools else -1) else svc["state"]

    # Jevin
    if JEV_DB is not None and JEV_DB.exists():
        try:
            jdb = ro(JEV_DB)
            row = jdb.execute(
                "select tool, choice, confidence, ts, cost from jev_decisions "
                "order by id desc limit 1"
            ).fetchone()
            total = jdb.execute("select count(*) from jev_decisions").fetchone()[0]
            if row:
                state["services"]["jevin"] = {
                    "id": "jev", "kind": "ambient", "name": "Jevin",
                    "state": "active",
                    "lastTool": f"{row[0]}→{row[1]} ({(row[2] or 0):.0%})",
                    "confidence": row[2],
                    "calls": total,
                }
        except sqlite3.Error:
            pass

    # ambient: heartbeat + gateways (sinais de vida dos ficheiros)
    hb = HEARTBEAT_FILE
    if hb is not None and hb.exists():
        try:
            hbd = json.loads(hb.read_text(encoding="utf-8"))
            state["services"]["heartbeat"] = {
                "id": "ambient:heartbeat", "kind": "ambient", "name": "heartbeat",
                "state": "idle",
                "lastTool": f"{len(hbd.get('last_notified', {}))} watchers",
                "calls": len(hbd.get("last_notified", {})),
            }
        except Exception:
            pass

    # ticker de eventos: últimos 15 tool calls
    for rid, sid, e in events[-15:]:
        title = display_event_title(e["title"] or "")
        state["events"].insert(0, {
            "rid": rid, "session": sid,
            "text": f"{e['name']}: {title}".strip()[:110],
            "status": e["status"],
        })

    # kanban: todas as sessões não-hidden classificadas (running/blocked/
    # review/closed). Corre no probe também — o hub re-aplica o closed dele.
    # Sessões GUI gravam na acp-messages db, não na sessions.db — o mtime
    # dessa db entra como actividade extra e a última tool_call como estado
    # real do turno (a db não escreve enquanto uma tool corre).
    try:
        acp_act, acp_tool = {}, {}
        for sid, p in acp_by_sid.items():
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            acp_act[sid] = mtime
            if now - mtime <= 3600 or session_locked(sid):
                st = acp_last_tool_status(p)
                if st:
                    acp_tool[sid] = st
        state["sessions"] = sessmon.collect_sessions(
            db, SESSION_LOCKS, now, sessmon.load_closed(KANBAN_FILE),
            activity=acp_act, acp_tool=acp_tool)
    except Exception as exc:
        state["sessions_error"] = str(exc)

    db.close()
    state["swap"] = collect_swap()
    return state


INDEX = (ROOT / "index.html").read_bytes()
KANBAN_INDEX = (ROOT / "kanban.html").read_bytes()
STATE_CACHE = {"ts": int(time.time()), "agents": [], "services": {}, "events": [], "loading": True}
STATE_LOCK = threading.Lock()
ECO_CACHE = {"eco": None, "ts": 0}
ECO_LOCK = threading.Lock()


def cached_state() -> dict:
    with STATE_LOCK:
        return STATE_CACHE


def cached_eco() -> dict:
    with ECO_LOCK:
        return ECO_CACHE["eco"]


def refresh_state_loop() -> None:
    global STATE_CACHE
    while True:
        try:
            next_state = collect_state()
        except Exception as exc:
            next_state = {"ts": int(time.time()), "agents": [], "services": {}, "events": [], "error": str(exc)}
        with STATE_LOCK:
            STATE_CACHE = next_state
        time.sleep(2)


def refresh_eco_loop() -> None:
    """Eco collect é mais pesado (probes HTTP + process table + scheduler)
    — corre num ciclo mais lento que o state loop."""
    while True:
        try:
            eco = collect_eco()
        except Exception as exc:
            eco = {"error": str(exc)}
        with ECO_LOCK:
            ECO_CACHE.update(eco=eco, ts=int(time.time()))
        time.sleep(12)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors_origin(self) -> str:
        """Cross-origin access is opt-in: OFFICE_CORS_ORIGIN, or loopback
        origins only (local dashboards on other ports). No wildcard."""
        configured = os.environ.get("OFFICE_CORS_ORIGIN", "").strip()
        if configured:
            return configured
        origin = self.headers.get("Origin", "")
        if re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
            return origin
        return ""

    def do_GET(self):
        if self.path.startswith("/api/state"):
            body = json.dumps(cached_state()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/api/eco-raw"):
            body = json.dumps(cached_eco() or {}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/api/eco"):
            body = json.dumps({"eco": cached_eco()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path in ("/", "/index.html"):
            body = INDEX
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path.split("?")[0] == "/kanban.html":
            body = KANBAN_INDEX
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            body = b"404"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        allow = self._cors_origin()
        if allow:
            self.send_header("Access-Control-Allow-Origin", allow)
            self.send_header("Vary", "Origin")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_POST(self):
        """POST /api/kanban {"id": sid, "action": "close"|"open"} — marca a
        sessão como fechada/reaberta no kanban (persiste em kanban.json)."""
        if not self.path.startswith("/api/kanban"):
            body = b"404"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            sid = str(payload.get("id") or "")[:80]
            action = str(payload.get("action") or "")
            if not sid or action not in ("close", "open"):
                raise ValueError("id + action=close|open required")
            out = sessmon.set_closed(KANBAN_FILE, sid, action == "close")
            body = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        allow = self._cors_origin()
        if allow:
            self.send_header("Access-Control-Allow-Origin", allow)
            self.send_header("Vary", "Origin")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="devin-office",
        description="Standalone read-only Devin Office dashboard "
                    "(sessions.db -> http://localhost:8788).")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args(argv)
    print(f"devin-office smoke daemon -> http://localhost:{args.port}")
    print(f"sessions.db: {SESSIONS_DB} (exists={SESSIONS_DB.exists()})")
    threading.Thread(target=refresh_state_loop, daemon=True).start()
    threading.Thread(target=refresh_eco_loop, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
