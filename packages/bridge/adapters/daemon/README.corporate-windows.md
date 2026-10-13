# devin-bridge intake — Corporate Windows

> New task — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

Same scheduled task as [README.windows.md](README.windows.md), plus the
constraints of a managed machine.

> Scope note: the bridge's function is delegated external compute — it
> spawns `devin acp`, which talks to Devin's backend. It sits outside the
> local-only guarantee and **does perform network I/O**, so the proxy
> notes below apply to every drain, not just install time.

## User scope, no admin

The task registers under the current user with `InteractiveToken` /
`LeastPrivilege` — it only runs while you are logged on and never writes
outside your profile. If `install-task.ps1` fails with **0x80070005
(access denied)**, your account or a GPO is blocking Task Scheduler (or
"Log on as a batch job" was revoked): the script surfaces that explicitly.
Ask IT for a user-scope scheduling exception; do not work around it by
requesting admin.

## Why logon + repeat instead of a fixed time

Corporate laptops suspend, hibernate and dock unpredictably. A fixed
clock-time trigger only catches up if `StartWhenAvailable` happens to
fire; machines that sleep through the slot drift silently. The shipped XML
uses a **logon trigger with a 5-minute repetition** — the equivalent of
`-AtLogOn` plus polling — so intake resumes whenever the session exists.

## Proxy / TLS inspection (Zscaler-class)

Install-time proxy notes live in the package's
[README.corporate-windows.md](../../README.corporate-windows.md). For the
scheduled task itself: every drain spawns `devin acp`, which needs
outbound HTTPS to Devin's backend **from inside Task Scheduler's
environment** — `setx`-persisted `HTTPS_PROXY`/`HTTP_PROXY` values are
visible to tasks, per-session `$env:` ones are not.

- Persist proxy vars for your user: `setx HTTPS_PROXY "http://proxy:port"`.
- If the corporate proxy intercepts TLS, point Node at the company CA:
  `setx NODE_EXTRA_CA_CERTS "C:\path\corp-root.pem"` (bridge is Node.js;
  `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` do nothing for it).
- Fail-closed behavior: with no usable proxy/CA the drain exits nonzero,
  the task file stays in `mailbox\inbox` (parseable) or moves to
  `mailbox\failed` — check `Get-ScheduledTaskInfo.LastTaskResult` and the
  failed dir rather than assuming silence means idle.

## Smoke check

```powershell
Get-ScheduledTask -TaskName 'devin-bridge-intake' | Get-ScheduledTaskInfo
Get-ChildItem "$env:LOCALAPPDATA\devin-bridge\mailbox\failed" -ErrorAction SilentlyContinue
```
