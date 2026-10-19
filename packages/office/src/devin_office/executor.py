#!/usr/bin/env python3
"""office-executor — ponte de controlo do devin-office (corre localmente).

Fala ACP (Agent Client Protocol) com um processo `devin acp` filho, usando a
credencial do Devin Desktop no diretório de dados da plataforma. Isto permite
controlar sessões: injetar prompts (session/load + session/prompt), criar
sessões headless (session/new + session/prompt) e interromper turns (session/cancel).

Protocolo de ficheiros (zero deps, resiste a restarts):
  office/cmd_inbox/<id>.json  → {"id","action","target","text","at"}
  office/outbox/<id>.json     → {"id","status","detail","session_id","at"}
  office/state/executor.pid   → pid vivo (heartbeat + guarda de instância única)
  office/state/spawns.json    → sessões criadas por nós {session_id: {...}}

Acções:
  message <session_id> <text>   — entrega texto a uma sessão. Se a sessão
                                  está locked (aberta na GUI/CLI), faz
                                  fallback p/ outbox/manual-<id>.txt e
                                  reporta "queued-manual".
  spawn <prompt>                — sessão headless nova em OFFICE_SPAWN_CWD
                                  (default: repo root). Aparece no office
                                  como DEVIN NN novo.
  kill <session_id>             — session/cancel (só sessões spawned por nós;
                                  sessões da GUI são owned pelo ACP server
                                  da GUI — não alcançáveis → "unsupported").
  kill <session_id>:<idx>       — worker/subagente: pede à sessão-mãe (se
                                  desbloqueada) para parar o subagente.
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
if __package__ in (None, ""):
    # Loose script run from a checkout (no install): make src/ importable so
    # the devin_office.* imports below resolve — same contract the old flat
    # sys.path.insert gave, pointed one level up at the src/ root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from devin_office.state import state_dir, spawn_cwd

ROOT = Path(__file__).resolve().parent
_STATE = state_dir()
INBOX = _STATE / "cmd_inbox"
OUTBOX = _STATE / "outbox"
STATEDIR = _STATE / "state"
PIDFILE = STATEDIR / "executor.pid"
SPAWNS = STATEDIR / "spawns.json"
_appdata = os.environ.get("APPDATA")
DATA_DIR = Path(
    os.environ.get("OFFICE_DATA_DIR")
    or (Path(_appdata) / "devin" if _appdata
        else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "devin")
)
CREDENTIALS = DATA_DIR / "credentials.toml"
SESSION_LOCKS = DATA_DIR / "cli" / "session_locks"


def _default_devin_exe() -> str:
    import shutil
    exe = shutil.which("devin")
    if exe:
        return exe
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return str(
            Path(local) / "Programs" / "Devin" / "resources" / "app"
            / "extensions" / "windsurf" / "devin" / "bin" / "devin.exe"
        )
    return "devin"


DEVIN_EXE = os.environ.get("OFFICE_DEVIN_EXE", _default_devin_exe())
SPAWN_CWD = str(spawn_cwd())
SPAWN_MODE = os.environ.get("OFFICE_SPAWN_MODE", "smart")  # modeId p/ spawns
PROMPT_TIMEOUT_S = float(os.environ.get("OFFICE_PROMPT_TIMEOUT", "900"))
CREATE_NO_WINDOW = 0x08000000


def ensure_dirs() -> None:
    for directory in (INBOX, OUTBOX, STATEDIR):
        directory.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    try:
        with open(_STATE / "executor.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def read_credentials():
    txt = CREDENTIALS.read_text(encoding="utf-8", errors="replace")
    key = re.search(r'windsurf_api_key\s*=\s*"?([^"\n]+)', txt)
    url = re.search(r'api_server_url\s*=\s*"?([^"\n]+)', txt)
    return (key.group(1) if key else None,
            url.group(1) if url else None)


def session_cwd(sid: str) -> str:
    """working_directory real da sessão, lido de sessions.db (necessário
    para session/load)."""
    import sqlite3
    db_path = CREDENTIALS.parent / "cli" / "sessions.db"
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        row = db.execute(
            "select working_directory from sessions where id=?", (sid,)
        ).fetchone()
        db.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return SPAWN_CWD


def session_locked(sid: str) -> bool:
    """True se o lock file da sessão está held por outro processo devin
    (aberta na GUI ou noutro devin acp). Nota: sessões hosted por ESTE
    executor também aparecem locked — tratadas via `mine`."""
    f = SESSION_LOCKS / f"{sid}.lock"
    if not f.exists():
        return False
    try:
        fd = os.open(str(f), os.O_RDWR)
        os.close(fd)
        return False
    except OSError:
        return True


def write_result(cid: str, status: str, detail: str = "", session_id=None):
    body = {"id": cid, "status": status, "detail": detail[:500],
            "session_id": session_id, "at": int(time.time())}
    tmp = OUTBOX / f"{cid}.tmp"
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUTBOX / f"{cid}.json")


class AcpError(Exception):
    pass


class AcpClient:
    """Cliente ACP mínimo sobre stdio (ndjson). Thread reader despacha
    respostas por id; pedidos server→cliente (permission/fs/terminal)
    são respondidos automaticamente."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [DEVIN_EXE, "acp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._id = 0
        self._id_lock = threading.Lock()
        self._pending = {}
        self._dead = threading.Event()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if "method" in msg and "id" in msg:
                    self._handle_server_request(msg)
                elif "id" in msg:
                    q = self._pending.get(msg["id"])
                    if q is not None:
                        q.put(msg)
        finally:
            self._dead.set()
            for q in list(self._pending.values()):
                q.put({"error": {"message": "acp process died"}})

    def _handle_server_request(self, msg):
        method = msg["method"]
        try:
            if method == "session/request_permission":
                opts = msg.get("params", {}).get("options", [])
                pick = next(
                    (o for o in opts
                     if "allow" in str(o.get("kind") or o.get("optionId", "")).lower()
                     and "always" not in str(o.get("kind") or o.get("optionId", "")).lower()),
                    None) or next(
                    (o for o in opts
                     if "allow" in str(o.get("kind") or o.get("optionId", "")).lower()),
                    opts[0] if opts else None)
                res = {"outcome": {"outcome": "cancelled"}} if not pick else {
                    "outcome": {"outcome": "selected",
                                "optionId": pick.get("optionId")}}
            else:
                res = None  # método desconhecido → erro
            if res is not None:
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": res})
            else:
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "error": {"code": -32601,
                                      "message": f"not implemented: {method}"}})
        except Exception:
            pass

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj).encode() + b"\n")
        self.proc.stdin.flush()

    def rpc(self, method, params=None, timeout=30):
        if self._dead.is_set():
            raise AcpError("acp process died")
        with self._id_lock:
            self._id += 1
            rid = self._id
        q = queue.Queue()
        self._pending[rid] = q
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})
            msg = q.get(timeout=timeout)
        except queue.Empty:
            raise AcpError(f"timeout waiting for {method}")
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            raise AcpError(msg["error"].get("message", "acp error"))
        return msg.get("result") or {}

    def handshake(self):
        self.rpc("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False,
                                          "writeTextFile": False},
                                   "terminal": False},
        })
        key, url = read_credentials()
        if not key:
            raise AcpError("no windsurf_api_key in credentials.toml")
        self.rpc("authenticate", {
            "methodId": "windsurf-api-key",
            "_meta": {"api_key": key, "api_server_url": url or ""},
        })

    def prompt(self, sid, text, timeout=PROMPT_TIMEOUT_S):
        return self.rpc("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": text}],
        }, timeout=timeout)

    def load(self, sid, cwd):
        return self.rpc("session/load", {
            "sessionId": sid, "cwd": cwd, "mcpServers": []}, timeout=120)

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


class Executor:
    def __init__(self):
        self.acp = AcpClient()
        self.mine = self._load_spawns()
        self.seen = set()   # ids já executados (dedup vs re-claim do hub)

    def _load_spawns(self):
        try:
            return json.loads(SPAWNS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_spawns(self):
        try:
            SPAWNS.write_text(json.dumps(self.mine), encoding="utf-8")
        except OSError:
            pass

    # ── actions ────────────────────────────────────────────
    def do_message(self, cid, sid, text, cwd=None):
        if sid in self.mine:
            # sessão é hosted por este executor — pode promptar directamente
            r = self.acp.prompt(sid, text)
            return ("delivered",
                    f"prompt ok (stopReason={r.get('stopReason')})", sid)
        if session_locked(sid):
            return self._manual_fallback(cid, sid, text)
        try:
            self.acp.load(sid, cwd or session_cwd(sid))
        except AcpError as exc:
            if "already open" in str(exc):
                return self._manual_fallback(cid, sid, text)
            raise
        r = self.acp.prompt(sid, text)
        return ("delivered",
                f"prompt ok (stopReason={r.get('stopReason')})", sid)

    def _manual_fallback(self, cid, sid, text):
        note = OUTBOX / f"manual-{cid}.txt"
        note.write_text(
            f"Para a sessão {sid} (está aberta na GUI/CLI — locked):\n\n{text}\n",
            encoding="utf-8")
        return ("queued-manual",
                f"session locked; escrito em {note.name} — cola lá",
                sid)

    def do_spawn(self, cid, text, cwd=None):
        r = self.acp.rpc("session/new",
                         {"cwd": cwd or SPAWN_CWD, "mcpServers": []},
                         timeout=60)
        sid = r.get("sessionId")
        if not sid:
            return ("error", "session/new sem sessionId", None)
        try:
            self.acp.rpc("session/set_mode",
                         {"sessionId": sid, "modeId": SPAWN_MODE}, timeout=15)
        except AcpError:
            pass
        self.mine[sid] = {"cmd_id": cid, "at": int(time.time()),
                          "prompt": text[:120]}
        self._save_spawns()
        r = self.acp.prompt(sid, text)
        return ("spawned",
                f"session {sid} terminou (stopReason={r.get('stopReason')})",
                sid)

    def do_kill(self, cid, target, text):
        # worker subagente: "<sid>:<idx>"
        if ":" in target:
            sid, _, idx = target.partition(":")
            if sid in self.mine:
                try:
                    self.acp.prompt(
                        sid,
                        f"Stop and do not resume the background subagent "
                        f"#{int(idx) + 1} ({text or 'the worker'}). Reply "
                        f"briefly.")
                    return ("interrupt-sent", "pedido de stop enviado ao pai",
                            sid)
                except AcpError as exc:
                    return ("error", str(exc), sid)
            if session_locked(sid):
                return ("unsupported",
                        ("worker vive dentro da sessão-mãe, que está locked "
                         "pela GUI — sem caminho de interrupt"), sid)
            try:
                self.acp.load(sid, session_cwd(sid))
                self.acp.prompt(
                    sid,
                    (f"Stop and do not resume the background subagent "
                     f"#{int(idx) + 1} ({text or 'the worker'}). Reply briefly."))
                return ("interrupt-sent", "pedido de stop enviado ao pai", sid)
            except AcpError as exc:
                if "already open" in str(exc):
                    return ("unsupported",
                            ("sessão-mãe aberta noutro processo (GUI) — "
                             "sem caminho de interrupt"), sid)
                return ("error", str(exc), sid)
        # sessão inteira
        if target in self.mine:
            try:
                self.acp.rpc("session/cancel", {"sessionId": target},
                             timeout=15)
                return ("interrupted", "session/cancel enviado", target)
            except AcpError as exc:
                return ("error", str(exc), target)
        return ("unsupported",
                ("só é possível interromper sessões spawned pelo office "
                 "(as da GUI pertencem ao ACP server dela)"), target)

    def dispatch(self, cmd):
        cid, action = cmd.get("id"), cmd.get("action")
        target, text = (cmd.get("target") or "").strip(), cmd.get("text") or ""
        try:
            if action == "message" and target and text:
                st, detail, sid = self.do_message(cid, target, text)
            elif action == "spawn" and text:
                st, detail, sid = self.do_spawn(cid, text)
            elif action == "kill" and target:
                st, detail, sid = self.do_kill(cid, target, text)
            else:
                st, detail, sid = "error", f"bad action/target: {action}", None
        except AcpError as exc:
            st, detail, sid = "error", str(exc), None
        except Exception as exc:
            st, detail, sid = "error", f"{type(exc).__name__}: {exc}", None
        write_result(cid, st, detail, sid)
        log(f"cmd {cid} {action} -> {st}: {detail[:100]}")

    def serve(self):
        self.acp.handshake()
        log("executor up — acp autenticado")
        write_result("_executor", "ready", "acp handshake ok")
        while True:
            try:
                PIDFILE.write_text(str(os.getpid()))
            except OSError:
                pass
            if self.acp._dead.is_set():
                log("acp died — exiting para respawn")
                return
            for f in sorted(INBOX.glob("*.json")):
                try:
                    cmd = json.loads(f.read_text(encoding="utf-8"))
                    f.unlink()
                except Exception:
                    continue
                cid = cmd.get("id", "?")
                if cid in self.seen or (OUTBOX / f"{cid}.json").exists():
                    continue
                self.seen.add(cid)
                write_result(cid, "running", "executing…")
                threading.Thread(target=self.dispatch, args=(cmd,),
                                 daemon=True).start()
            time.sleep(0.7)


def pid_alive(pid: int) -> bool:
    """Windows-safe existence check (os.kill(pid,0) terminaria o processo)."""
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        # fallback POSIX
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def already_running() -> bool:
    try:
        pid = int(PIDFILE.read_text().strip())
        return pid_alive(pid)
    except Exception:
        return False


_MUTEX_HANDLE = None
_MUTEX_NAME = "Local\\DevinOfficeExecutor"


def acquire_singleton() -> bool:
    """Race-free singleton guard.

    The pidfile is TOCTOU: two spawns (up.pyw + probe lazy-spawn) pass the
    check before either writes the file. A Windows named mutex is atomic —
    the kernel arbitrates. POSIX keeps the pidfile check.
    """
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return not already_running()
    import ctypes
    h = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if not h:
        return not already_running()
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(h)
        return False
    _MUTEX_HANDLE = h
    return True


def singleton_alive() -> bool:
    """True when an executor is already alive (non-acquiring — probe-safe)."""
    if sys.platform != "win32":
        return already_running()
    import ctypes
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, _MUTEX_NAME)
    if not h:
        return False
    ctypes.windll.kernel32.CloseHandle(h)
    return True


def main():
    ensure_dirs()
    if not acquire_singleton():
        return
    try:
        Executor().serve()
    except Exception as exc:
        log(f"executor fatal: {exc}")
        write_result("_executor", "error", str(exc))
        try:
            PIDFILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
