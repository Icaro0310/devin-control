# devin-office — daemon adapters

Keep-alive faces for `devin_office/daemon.py`, the standalone dashboard
server. Unlike the oneshot units elsewhere in this ecosystem, devin-office
**is** a resident process: it serves the pixel office and `/api/state` on
`127.0.0.1:8788` while polling Devin's local stores **read-only**
(`sqlite3.connect(...?mode=ro)`). "The daemon" already exists — these
adapters only make it survive reboots/logins instead of a manual
`python3 src/devin_office/daemon.py --port 8788` (or `devin-office
--port 8788` once the package is installed).

| Platform | Files |
| --- | --- |
| Linux (systemd user) | `devin-office.service` |
| Windows | `devin-office.task.xml` + `install-task.ps1` / `remove-task.ps1` |

Guides: [Linux](README.linux.md) ·
[Windows](README.windows.md) ·
[Corporate Windows](README.corporate-windows.md)

What runs: `python3 src/devin_office/daemon.py --port 8788` (Linux) /
`py -3 src\devin_office\daemon.py --port 8788` (Windows) — the exact
commands from the package README; the module self-bootstraps so a bare
checkout needs no install. The units add no logic; they only keep the
process alive.

> **Status:** these units are new — the 7-day no-silent-failure observation
> window started **2026-10-12** and has **not** completed. They are
> validated (unit syntax, XML parse, documented smoke checks) but not yet
> burn-in proven; if you enable them early, verify with the smoke-check
> section of your platform guide.

> Unofficial community project. Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.
