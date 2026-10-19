"""devin-office — local-first dashboard for live Devin sessions.

Reads Devin's local session stores (``sessions.db``, ACP message DBs)
read-only and renders active sessions, subagents and tool calls as a live
circuit-board view. Standard library only.

Console scripts:

* ``devin-office``          — standalone daemon (dashboard + /api/state)
* ``devin-office-hub``      — private hub for split mode (probe → hub)
* ``devin-office-probe``    — lean probe that POSTs state to the hub
* ``devin-office-executor`` — opt-in ACP control process (message/spawn/kill)
* ``devin-office-procmap``  — process → session attribution (read-only)
"""

__version__ = "0.1.0"
