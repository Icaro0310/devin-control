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

The task's console output is not persisted by Task Scheduler. For a
durable notification log, edit the action arguments to redirect, e.g.
`/c devin-switch doctor >> "%USERPROFILE%\.devin\switch-drift.log" 2>&1`.

## Smoke check — did it run without silent failure?

```powershell
Start-ScheduledTask -TaskName 'devin-switch-drift'
Get-ScheduledTask -TaskName 'devin-switch-drift' | Get-ScheduledTaskInfo
# LastTaskResult 0 = doctor ran clean (or drift only, which is WARN-level);
# a sanity FAIL exits 1 and shows up here.
devin-switch doctor   # same report, interactively
```

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\remove-task.ps1
# or: schtasks /delete /tn "devin-switch-drift" /f
```
