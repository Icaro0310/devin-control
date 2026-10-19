# devin-office — Scheduled Task (Windows)

> New task — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

`devin-office.task.xml` registers a **user-scope** task with a single
**logon trigger**: while you are logged on it keeps
`py -3 <checkout>\src\devin_office\daemon.py --port 8788` running — the
dashboard on http://localhost:8788. (The module self-bootstraps its
imports, so a bare checkout needs no install; with
`uv tool install`/`pip install -e` the action can call `devin-office`
directly instead.) Unlike the other tasks in this ecosystem this one
*is* the resident process, so its `ExecutionTimeLimit` is `PT0S`
(unlimited) and Task Scheduler restarts it up to 3 times on failure.

## Install

```powershell
cd adapters\daemon
powershell -ExecutionPolicy Bypass -File .\install-task.ps1
```

`install-task.ps1` resolves the package checkout from its own location,
substitutes the `__OFFICE_CHECKOUT__` placeholder in the XML, and
registers via `Register-ScheduledTask` in user scope — **no admin
required**. It warns first if the `py` launcher is not on `PATH`.

To import the XML by hand, replace `__OFFICE_CHECKOUT__` with your
checkout path first, then:

```powershell
schtasks /create /tn "devin-office" /xml devin-office.task.xml /f
```

## Smoke check — did it run without silent failure?

```powershell
Start-ScheduledTask -TaskName 'devin-office'
Get-ScheduledTask -TaskName 'devin-office' | Get-ScheduledTaskInfo
# LastTaskResult 0 = the launcher ran; the daemon then stays resident.

Invoke-WebRequest http://localhost:8788/api/state -UseBasicParsing | Select-Object -ExpandProperty StatusCode
# 200 = dashboard API is being served.
```

If `LastTaskResult` is nonzero the usual cause is a missing `py`
launcher or a checkout path that moved — re-run `install-task.ps1` from
the new location and it re-patches the path (`-Force` overwrites).

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\remove-task.ps1
# or: schtasks /delete /tn "devin-office" /f
```

The devin-control checkout is left in place.
