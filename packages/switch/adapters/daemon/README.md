# devin-switch — daemon adapters

Periodic **drift check** for the live Devin config: `devin-switch doctor`
sanity-checks hooks/MCP shapes and ranks the closest known profile by
file distance. Notification only — drift is reported, never re-applied
(`devin-switch use <profile> --apply` stays a manual, reviewed step).

| Platform | Files |
| --- | --- |
| Linux (systemd user) | `devin-switch-drift.service` + `.timer` |
| Windows | `devin-switch-drift.task.xml` + `install-task.ps1` / `remove-task.ps1` |

Guides: [Linux](README.linux.md) ·
[Windows](README.windows.md) ·
[Corporate Windows](README.corporate-windows.md)

What runs: `devin-switch doctor` — read-only; the units only schedule the
package's own CLI.

> **Status:** these units are new — the 7-day no-silent-failure observation
> window started **2026-10-12** and has **not** completed. They are
> validated (unit syntax, XML parse, documented smoke checks) but not yet
> burn-in proven; if you enable them early, verify with the smoke-check
> section of your platform guide.

> Unofficial community project. Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.
