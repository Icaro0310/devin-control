# devin-switch drift — systemd user timer (Linux)

> New units — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

- **`devin-switch-drift.timer`** runs the service daily at 09:00 with
  `Persistent=true` — a missed slot (suspend, powered off) catches up at
  next activation instead of silently skipping.
- **`devin-switch-drift.service`** (`Type=oneshot`) runs
  `devin-switch doctor` and leaves the report in the journal.

The check is read-only, so the service is hardened to match:
`ProtectSystem=strict`, `ProtectHome=read-only`, `NoNewPrivileges`,
`PrivateTmp` — it can read the Devin roots but write nothing.

## Install

```bash
mkdir -p ~/.config/systemd/user
cp devin-switch-drift.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now devin-switch-drift.timer
```

If `devin-switch` is not under `~/.local/bin` or the system prefixes,
extend `Environment=PATH=...` in the service.

## Smoke check — did it run without silent failure?

```bash
# Timer armed:
systemctl --user list-timers devin-switch-drift.timer

# Run it now and read the notification:
systemctl --user start devin-switch-drift.service
journalctl --user -u devin-switch-drift.service -n 30 --no-pager
```

What drift looks like in the journal:

```
PASS config.json — valid JSON
...
closest profile: personal (2 files away)
```

`exactly matches` means no drift; `N files away` means the live config
diverged from the nearest profile — decide by hand whether to
`devin-switch use <profile> --apply`. A `FAIL` line makes the service exit
nonzero, which surfaces in `systemctl --user status`.

## Uninstall

```bash
systemctl --user disable --now devin-switch-drift.timer
rm ~/.config/systemd/user/devin-switch-drift.{service,timer}
systemctl --user daemon-reload
```
