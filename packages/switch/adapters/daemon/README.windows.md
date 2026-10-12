# devin-switch drift — Scheduled Task (Windows)

> New task — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

`devin-switch-drift.task.xml` registers a **user-scope** task running
`devin-switch doctor` at logon and daily. It is notification only: the
report is the task's output, and re-applying a profile stays manual.

## Install

```powershell
cd adapters\daemon
powershell -ExecutionPolicy Bypass -File .\install-task.ps1
```

No admin needed. Equivalent without the script:

```powershell
schtasks /create /tn "devin-switch-drift" /xml devin-switch-drift.task.xml /f
```

Task Scheduler discards console output, so the shipped action appends
the doctor report to `%LOCALAPPDATA%\devin-switch\drift.log` (the cmd
wrapper creates the dir on first run) — the notification survives in a
file, not just an exit code.

## Smoke check — did it run without silent failure?

```powershell
Start-ScheduledTask -TaskName 'devin-switch-drift'
Get-ScheduledTask -TaskName 'devin-switch-drift' | Get-ScheduledTaskInfo
# LastTaskResult 0 = doctor ran clean (or drift only, which is WARN-level);
# a sanity FAIL exits 1 and shows up here.
Get-Content "$env:LOCALAPPDATA\devin-switch\drift.log" -Tail 30   # retained report
devin-switch doctor   # same report, interactively
```

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\remove-task.ps1
# or: schtasks /delete /tn "devin-switch-drift" /f
```
