# devin-bridge intake — systemd user units (Linux)

> New units — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

Three units work together:

- **`devin-bridge-intake.path`** watches `*.json` in
  `~/.local/state/devin-bridge/mailbox/inbox` and fires the service the
  moment a task file lands (the fast path). It watches a glob, not
  directory-emptiness, so stray non-JSON files can't retrigger empty
  drains until systemd's trigger limit stops the watch — and producers
  must publish atomically (write a temp name, rename to `.json`).
- **`devin-bridge-intake.timer`** re-runs the drain ~2 minutes after each
  run finishes (the fallback for suspend, network filesystems, or tasks
  dropped while the path unit was off).
- **`devin-bridge-intake.service`** (`Type=oneshot`) runs
  `devin-bridge intake` — the same command you run by hand.

The service is hardened: `NoNewPrivileges`, `PrivateTmp`,
`ProtectSystem=strict`. `StateDirectory=devin-bridge` has systemd create
`~/.local/state/devin-bridge` (and parents) at service start and keeps it
writable — no missing-dir setup failure on a fresh profile.
`ConfigurationDirectory=Devin devin` does the same for the Devin config
roots the spawned `devin acp` may write. The Devin data root
(`~/.local/share/devin`) is whitelisted as `-`-optional: absent entries
are skipped instead of failing mount-namespace setup — a drain on a box
with no Devin data dir then fails visibly (journal + `mailbox/failed/`),
which is the honest outcome anyway.

## What the scheduled drain sees (and doesn't)

- **Policy:** `loadPolicy()` reads `<cwd>/policy.json` — the unit's
  `WorkingDirectory=%h` makes that `~/policy.json`. Intake loads ONE
  policy per drain; a task's `task.repo` `policy.json` is NOT consulted
  (same semantics as running `devin-bridge intake` from your home dir).
  Pin an explicit file with `--policy %h/policy.json` if your policy
  lives elsewhere.
- **Session map:** the unit passes
  `--sessions-file ~/.local/state/devin-bridge/sessions.json` — inside
  the writable state dir — while a manual run defaults to
  `./.sessions.json`. Scheduled and interactive intakes therefore use
  different maps unless you pass the same flag by hand; pass it if you
  want repo→session continuity across both.

## Install

```bash
# 1. The inbox must exist before the path unit can watch it
#    (or run `devin-bridge intake` once — it creates all stage dirs).
mkdir -p ~/.local/state/devin-bridge/mailbox/inbox

# 2. Install the units
mkdir -p ~/.config/systemd/user
cp devin-bridge-intake.{service,path,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now devin-bridge-intake.path devin-bridge-intake.timer
```

If `devin-bridge` is not under `~/.local/bin`, `~/.npm-global/bin`,
`/usr/local/bin` or `/usr/bin`, extend the `Environment=PATH=...` line in
the `.service` (`npm config get prefix` shows where npm puts global bins).

For unattended runs edit the service `ExecStart` to add `--yes`
(auto-approves policy "ask" prompts — read your `policy.json` first;
without it, "ask" decisions fail closed and the task file lands in
`mailbox/failed/`).

## Smoke check — did it run without silent failure?

```bash
# Path + timer armed:
systemctl --user list-timers devin-bridge-intake.timer
systemctl --user status devin-bridge-intake.path

# Trigger one drain by hand — an empty inbox is a cheap no-op, so this
# exercises ExecStart/PATH/hardening without queuing any task:
systemctl --user start devin-bridge-intake.service
journalctl --user -u devin-bridge-intake.service -n 20 --no-pager

# Exercise parse + mailbox plumbing with NO session and NO cost: dry-run
# a task file in a THROWAWAY state dir. Do NOT drop test files into the
# real inbox — the armed .path unit drains them for real.
tmp=$(mktemp -d)
mkdir -p -m 700 "$tmp/mailbox/inbox"
echo '{"task":"ping"}' > "$tmp/mailbox/inbox/smoke.json"
devin-bridge intake --state-dir "$tmp" --dry-run   # expect status "dry-run"
rm -rf "$tmp"
```

`--dry-run` parses the file and reports `status: "dry-run"` without
spawning `devin acp` — no session is created and nothing is billed. To
exercise the FULL pipeline (policy gate, `devin acp` spawn, mailbox
bookkeeping) drop a real task in the real inbox — knowing it creates a
real Devin session (cost) and lands in `done/`/`failed/`; an "ask"
decision without `--yes` fails closed into `failed/` by design.

A crash-looping service shows as `activating (auto-restart)`, never in
`systemctl --user list-units --state=failed` — check `NRestarts` in
`systemctl --user show devin-bridge-intake.service -p NRestarts` if output
looks absent.

## Headless check

`./verify.sh` in this directory lints the units (`systemd-analyze
verify` when available), parses the task XML, and cross-checks that the
ps1 scripts reference the shipped task names — the same checks the
package's `tests/daemon-units.test.js` run in CI.

## Uninstall

```bash
systemctl --user disable --now devin-bridge-intake.path devin-bridge-intake.timer
rm ~/.config/systemd/user/devin-bridge-intake.{service,path,timer}
systemctl --user daemon-reload
```

The mailbox dir under `~/.local/state/devin-bridge` is left in place.
