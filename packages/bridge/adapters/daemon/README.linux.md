# devin-bridge intake — systemd user units (Linux)

> New units — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

Three units work together:

- **`devin-bridge-intake.path`** watches `~/.local/state/devin-bridge/mailbox/inbox`
  and fires the service the moment a task file lands (the fast path).
- **`devin-bridge-intake.timer`** re-runs the drain ~2 minutes after each
  run finishes (the fallback for suspend, network filesystems, or tasks
  dropped while the path unit was off).
- **`devin-bridge-intake.service`** (`Type=oneshot`) runs
  `devin-bridge intake` — the same command you run by hand.

The service is hardened: `NoNewPrivileges`, `PrivateTmp`,
`ProtectSystem=strict` with writes whitelisted to the bridge state dir
(`~/.local/state/devin-bridge`) and the Devin data/config roots
(`~/.local/share/devin`, `~/.config/devin`, `~/.config/Devin`) that the
spawned `devin acp` client needs.

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

# Drop a dry-run file in the inbox, then read the last drain:
echo '{"prompt":"ping"}' > ~/.local/state/devin-bridge/mailbox/inbox/smoke.json
sleep 3
journalctl --user -u devin-bridge-intake.service -n 20 --no-pager
ls ~/.local/state/devin-bridge/mailbox/done/ ~/.local/state/devin-bridge/mailbox/failed/
```

A crash-looping service shows as `activating (auto-restart)`, never in
`systemctl --user list-units --state=failed` — check `NRestarts` in
`systemctl --user show devin-bridge-intake.service -p NRestarts` if output
looks absent.

## Uninstall

```bash
systemctl --user disable --now devin-bridge-intake.path devin-bridge-intake.timer
rm ~/.config/systemd/user/devin-bridge-intake.{service,path,timer}
systemctl --user daemon-reload
```

The mailbox dir under `~/.local/state/devin-bridge` is left in place.
