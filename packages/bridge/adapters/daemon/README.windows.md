# devin-bridge intake — Scheduled Task (Windows)

> New task — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

`devin-bridge-intake.task.xml` registers a **user-scope** task that fires
at logon and repeats every 5 minutes, running `devin-bridge intake` — the
same one-shot mailbox drain you run by hand. The bridge has no listener by
design; the task is its Windows face.

The mailbox lives under `%LOCALAPPDATA%\devin-bridge\mailbox\inbox`.

## Install

```powershell
cd adapters\daemon
powershell -ExecutionPolicy Bypass -File .\install-task.ps1
```

`install-task.ps1` imports the XML via `Register-ScheduledTask` in user
scope — **no admin required**. It warns first if `devin-bridge` is not on
`PATH` (`npm config get prefix` shows the global bin dir).

Equivalent one-liner without the script:

```powershell
schtasks /create /tn "devin-bridge-intake" /xml devin-bridge-intake.task.xml /f
```

For unattended runs, edit the task action (or the XML) to
`devin-bridge intake --yes` — auto-approves policy "ask" prompts; read
your `policy.json` first, since without `--yes` every "ask" fails closed
and the file lands in `mailbox\failed`.

## Smoke check — did it run without silent failure?

```powershell
# Task armed and last result:
Get-ScheduledTask -TaskName 'devin-bridge-intake' | Get-ScheduledTaskInfo
# LastTaskResult 0 = clean run; the inbox drain writes mailbox\done|failed.

# Drop a task file and watch it move:
'{"task":"ping"}' | Set-Content "$env:LOCALAPPDATA\devin-bridge\mailbox\inbox\smoke.json"
Start-ScheduledTask -TaskName 'devin-bridge-intake'
Get-ChildItem "$env:LOCALAPPDATA\devin-bridge\mailbox\done","$env:LOCALAPPDATA\devin-bridge\mailbox\failed"
```

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\remove-task.ps1
# or: schtasks /delete /tn "devin-bridge-intake" /f
```

The mailbox dir under `%LOCALAPPDATA%\devin-bridge` is left in place.
