#!/usr/bin/env python3
"""sessmon — session kanban collector for devin-office.

Classifies every non-hidden session in sessions.db into board columns:
  running  — lock held + recent activity, turn still open
  blocked  — Devin is waiting on the user (permission prompt, question,
             or the user's last message never got a reply)
  review   — turn ended cleanly; session open awaiting review/close
  closed   — marked done via POST /api/kanban (persisted in kanban.json
             next to whoever serves /api/state: daemon or hub)

stdlib only. collect_sessions() takes an open read-only connection so the
caller (daemon.collect_state) decides the DB path.
"""
import json
import os
import re
import time
from pathlib import Path

RUN_WINDOW_S = 120      # lock held + activity within 2min → actively working
RUN_GRACE_S = 45        # no lock but this fresh → still starting/flapping
QUESTION_TAIL = 400     # chars of the last assistant text scanned for a question
CLOSED_LIMIT = 50
STALE_REVIEW_S = 3 * 86400   # review parado há >3 dias → closed automático
AUTO_SCAN_LIMIT = 5000       # msgs recentes onde se procura input humano
HUMAN_MARK = "chisel/client-message-id"  # user msgs da GUI/CLI têm isto;
                                         # scripts/ACP/heartbeat não

# sid → (last_activity_at, auto). O veredito só muda quando a sessão recebe
# msgs novas, por isso cacheia-se — o scan é o pior caso (sessões de
# automação têm dezenas de milhares de msgs).
_auto_cache: dict = {}


def is_automation(con, sid: str, last_act: int) -> bool:
    """True se nunca houve input humano nesta sessão: toda user message
    sem chisel/client-message-id veio de script (heartbeat, juiz, cron…).
    """
    ent = _auto_cache.get(sid)
    if ent and ent[0] == last_act:
        return ent[1]
    row = con.execute(
        "select 1 from (select chat_message from message_nodes "
        "where session_id=? order by row_id desc limit ?) "
        "where chat_message like ? limit 1",
        (sid, AUTO_SCAN_LIMIT, f"%{HUMAN_MARK}%")).fetchone()
    auto = row is None
    _auto_cache[sid] = (last_act, auto)
    return auto

QUESTION_RE = re.compile(
    r"(\?\s*$|quer que eu|diga |posso |deseja|gostaria|precisa que eu|"
    r"should i|want me to|let me know|do you want)",
    re.IGNORECASE,
)


def lock_held(locks_dir: Path, sid: str) -> bool:
    """True se a sessão está aberta num processo devin vivo.

    POSIX: os.open(O_RDWR) quase nunca falha (locks são advisory) — usa-se
    flock exclusivo não-bloqueante. Windows: o próprio open falha enquanto
    o holder mantém o ficheiro bloqueado.
    """
    f = Path(locks_dir) / f"{sid}.lock"
    if not f.exists():
        return False
    if os.name == "posix":
        import fcntl
        try:
            fd = os.open(str(f), os.O_RDWR)
        except OSError:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        return False
    try:
        fd = os.open(str(f), os.O_RDWR)
        os.close(fd)
        return False
    except OSError:
        return True


def _jload(s):
    try:
        return json.loads(s)
    except Exception:
        return None


def _msg_text(d: dict) -> str:
    """Extrai texto do chat_message (content lista de blocos ou str)."""
    content = d.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type", "text") == "text"
        )
    return str(content or "")


def classify(locked: bool, age_s: float, node: dict,
             last_tool_status: str | None, gui_active: bool = False,
             acp_status: str | None = None) -> tuple[str, str]:
    """→ (status, reason). reason é a pista mostrada no card.

    gui_active: a acp-messages db da sessão (Desktop) foi escrita dentro da
    janela — o processo está vivo e a trabalhar mesmo sem lock CLI.
    acp_status: status da última tool_call na acp db (só passado quando a
    db está fresca ou a sessão locked) — autoritativo para sessões GUI.
    """
    if last_tool_status == "pending":
        return "blocked", "approval"
    # GUI: tool_call a correr na acp db = working, mesmo sem writes novos
    # (a db não grava enquanto a tool corre — ex.: sleep/watch longos)
    if acp_status == "in_progress":
        return "running", "working"
    role = (node or {}).get("role")
    has_tools = bool((node or {}).get("has_tools"))
    recent = age_s <= RUN_WINDOW_S
    asked = False
    if role == "assistant" and not has_tools:
        tail = (node or {}).get("text", "")[-QUESTION_TAIL:]
        asked = bool(QUESTION_RE.search(tail))
    # turno ainda aberto: tools em curso, in_progress, user acabou de falar,
    # ou role=tool (resultado gravado — o agente ainda não respondeu)
    turn_open = (has_tools or last_tool_status == "in_progress"
                 or role in ("user", "tool"))
    # lock HELD ou acp db fresca + atividade recente = processo vivo a
    # escrever → working, mesmo que o último node pareça fechado.
    # tool in_progress antiga com lock = tool longa em curso.
    if (locked or gui_active) and (recent or last_tool_status == "in_progress"):
        if asked:
            return "blocked", "question"
        return "running", "working" if role != "user" else "reply pending"
    if recent and turn_open and age_s <= RUN_GRACE_S:
        return "running", "working" if role != "user" else "reply pending"
    if asked:
        return "blocked", "question"
    if role == "assistant" and not has_tools:
        if last_tool_status == "failed":
            return "review", "last tool failed"
        return "review", "done"
    if role == "user" and (node or {}).get("human"):
        # o utilizador falou e o Devin nunca respondeu (sessão morta/idle)
        return "blocked", "unanswered"
    if role == "user":
        # última msg veio de script (sem client-id) — não espera o user
        return "review", "ended"
    if locked:
        return "review", "idle"
    return "review", "ended"


def collect_sessions(con, locks_dir: Path, now: float | None = None,
                     closed: set | None = None, limit: int = 200,
                     activity: dict | None = None,
                     acp_tool: dict | None = None) -> list:
    """Todas as sessões não-hidden, mais recente primeiro, já classificadas.

    `activity`: sid → epoch de actividade extra (ex.: mtime da acp-messages
    db da sessão GUI — a sessions.db atrasa-se em sessões do Desktop).
    `acp_tool`: sid → status da última tool_call na acp db — autoritativo
    para sessões GUI (a db não escreve enquanto a tool corre).
    """
    now = now if now is not None else time.time()
    closed = closed or set()
    activity = activity or {}
    acp_tool = acp_tool or {}
    rows = con.execute(
        "select id, title, model, agent_mode, last_activity_at, "
        "working_directory, created_at from sessions "
        "where coalesce(hidden, 0)=0 order by last_activity_at desc limit ?",
        (limit,),
    ).fetchall()
    out = []
    for sid, title, model, mode, last_act, cwd, created in rows:
        node = {}
        row = con.execute(
            "select chat_message from message_nodes where session_id=? "
            "order by row_id desc limit 1", (sid,)).fetchone()
        if row:
            d = _jload(row[0]) or {}
            ext = ((d.get("metadata") or {}).get("extensions") or {})
            node = {"role": d.get("role"),
                    "has_tools": bool(d.get("tool_calls")),
                    "human": HUMAN_MARK in ext,
                    "text": _msg_text(d)}
        tool_row = con.execute(
            "select tool_call_update_json from tool_call_state "
            "where session_id=? order by rowid desc limit 1", (sid,)).fetchone()
        last_tool_status = (
            (_jload(tool_row[0]) or {}).get("status") if tool_row else None)
        # GUI: o estado da acp db ganha — a sessions.db pode estar atrasada
        acp_st = acp_tool.get(sid)
        last_tool_status = acp_st or last_tool_status
        locked = lock_held(locks_dir, sid)
        acp_ts = activity.get(sid, 0)
        gui_active = acp_ts > (last_act or 0) and now - acp_ts <= RUN_WINDOW_S
        eff_act = max(last_act or 0, acp_ts)
        status, reason = classify(locked, now - eff_act, node,
                                  last_tool_status, gui_active, acp_st)
        if is_automation(con, sid, last_act or 0):
            # sessão aberta por automação (Devin/scripts) — nunca espera
            # input humano, não pertence ao fluxo do utilizador
            status, reason = "closed", "automation"
        elif status == "review" and now - eff_act > STALE_REVIEW_S:
            status, reason = "closed", "stale >3d"
        if sid in closed:
            status, reason = "closed", "closed by user"
        out.append({
            "id": sid,
            "title": (title or "")[:90],
            "project": Path(cwd).name if cwd else "",
            "model": model or "?",
            "mode": mode or "",
            "status": status,
            "reason": reason,
            "locked": locked,
            "lastActivity": eff_act,
            "ageS": int(now - eff_act),
        })
    # closed só precisa do rabo recente — o resto fica para trás
    n_closed = sum(1 for s in out if s["status"] == "closed")
    if n_closed > CLOSED_LIMIT:
        seen = 0
        out = [s for s in out
               if s["status"] != "closed" or (seen := seen + 1) <= CLOSED_LIMIT]
    return out


# ---------- persistência do "closed" (quem serve /api/state) ----------

def load_closed(kanban_file: Path) -> set:
    try:
        data = json.loads(Path(kanban_file).read_text(encoding="utf-8"))
        return set((data.get("closed") or {}).keys())
    except Exception:
        return set()


def set_closed(kanban_file: Path, sid: str, close: bool) -> dict:
    """Marca/desmarca closed. Write atómico (.tmp + replace)."""
    kanban_file = Path(kanban_file)
    try:
        data = json.loads(kanban_file.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    closed_map = data.setdefault("closed", {})
    if close:
        closed_map[sid] = int(time.time())
    else:
        closed_map.pop(sid, None)
    tmp = kanban_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(kanban_file)
    return {"ok": True, "closed": sorted(closed_map.keys())}


def apply_closed(sessions: list, closed: set) -> list:
    """Reescreve status→closed nos ids marcados (lado do hub/standalone)."""
    if not closed:
        return sessions
    for s in sessions:
        if s.get("id") in closed:
            s["status"] = "closed"
            s["reason"] = "closed by user"
    return sessions
