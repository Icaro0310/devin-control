# devin-switch drift — Corporate Windows

> New task — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

Same scheduled task as [README.windows.md](README.windows.md), plus the
constraints of a managed machine.

## User scope, no admin

User-scope registration with `InteractiveToken` / `LeastPrivilege` — no
elevation, nothing outside your profile. If `install-task.ps1` fails with
**0x80070005 (access denied)**, a GPO is blocking Task Scheduler or "Log
on as a batch job" was revoked — ask IT for a user-scope scheduling
exception rather than requesting admin.

## Trigger choice under suspend

The XML ships a **logon trigger plus a daily calendar trigger** with
`StartWhenAvailable` — the `-AtLogOn`-style catch-up matters on machines
that sleep through the 09:00 slot; without a second trigger the check
would quietly never run on a suspended laptop.

## Network: none

`devin-switch doctor` reads local files only (`config.json`,
`mcp_config.json`, `User\settings.json` under `%APPDATA%\devin` /
`%APPDATA%\Devin`) — no network I/O, so no proxy or TLS-inspection
configuration is needed for the task itself.

## Smoke check

```powershell
Get-ScheduledTask -TaskName 'devin-switch-drift' | Get-ScheduledTaskInfo
```
