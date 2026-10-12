# devin-office — Corporate Windows

> New task — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

Same scheduled task as [README.windows.md](README.windows.md), plus the
constraints of a managed machine.

## User scope, no admin

The task registers under the current user with `InteractiveToken` /
`LeastPrivilege` — it only runs while you are logged on and never writes
outside your profile. If `install-task.ps1` fails with **0x80070005
(access denied)**, a GPO is blocking Task Scheduler or "Log on as a batch
job" was revoked: ask IT for a user-scope scheduling exception; do not
work around it by requesting admin. Endpoint agents that kill unsigned
`schtasks`/`Register-ScheduledTask` calls surface the same error code.

## Why a logon trigger

A resident dashboard has no schedule to miss — the single `-AtLogOn`-style
trigger just starts `daemon.py` with your session. There is deliberately
no fixed-time trigger: on a suspended laptop a clock slot would keep a
stale task entry while the dashboard itself stayed down.

## Network: loopback only

The daemon binds `127.0.0.1:8788` and its eco probes only call
`127.0.0.1` health endpoints — **no outbound traffic**, so the corporate
proxy and TLS-inspection notes in the package's
[README.corporate-windows.md](../../README.corporate-windows.md) apply
only at install/dev time (pip, git), not to the task itself. If you set
`OFFICE_ECO_URL` to reach a hub off this machine, that exception is on
you — expect the proxy vars and CA to matter at that point.

One caveat: `daemon.py` binds an OS socket, which on hardened endpoints
can trip a personal-firewall/EDR prompt the first time it runs. The bind
is loopback-only, but if your EDR policy blocks *any* listening socket,
the task will die immediately — check
`Get-ScheduledTaskInfo.LastTaskResult` and the EDR console rather than
assuming silence means idle.

## Smoke check

```powershell
Get-ScheduledTask -TaskName 'devin-office' | Get-ScheduledTaskInfo
Invoke-WebRequest http://localhost:8788/api/state -UseBasicParsing | Select-Object -ExpandProperty StatusCode
```
