# devin-bridge — daemon adapters

Scheduled/triggered faces of `devin-bridge intake`, the mailbox drain.
The bridge has **no listener by design** (BR-3: untrusted input arrives as
task files in `<state-dir>/mailbox/inbox/`; the policy gates every step),
so "the daemon" is a trigger around the existing one-shot CLI — not a
resident process.

| Platform | Files |
| --- | --- |
| Linux (systemd user) | `devin-bridge-intake.service` + `.path` + `.timer` |
| Windows | `devin-bridge-intake.task.xml` + `install-task.ps1` / `remove-task.ps1` |

Guides: [Linux](README.linux.md) ·
[Windows](README.windows.md) ·
[Corporate Windows](README.corporate-windows.md)

What runs: `devin-bridge intake` — drains inbox JSON tasks into Devin ACP
sessions. The units never reimplement bridge logic; they only schedule the
package's own CLI.

> **Status:** these units are new — the 7-day no-silent-failure observation
> window started **2026-10-12** and has **not** completed. They are
> validated (unit syntax, XML parse, documented smoke checks) but not yet
> burn-in proven; if you enable them early, verify with the smoke-check
> section of your platform guide.

> Unofficial community project. Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.
