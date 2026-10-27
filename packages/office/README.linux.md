# devin-office, Linux guide

This guide covers Linux setup only. See [README.md](README.md) for features, shared commands, limitations, and the safety model.

Linux uses the extended runtime: local execution plus optional Devin VM/QwenPaw delegation when this artifact supports it.

## Prerequisites

- `uv` and Python 3.10 or newer; `uv` can manage Python.

## Install

Run the standalone read-only dashboard — installed CLI, or
straight from a repository checkout:

```bash
uv tool install 'devin-office'
devin-office --port 8788  # installed package
python3 packages/office/src/devin_office/daemon.py --port 8788  # checkout
```

Open `http://localhost:8788`; the process stops with Ctrl+C.

## Devin paths

Session data normally lives under `${XDG_DATA_HOME:-$HOME/.local/share}/devin/cli/`; UI state and ACP stores under `${XDG_CONFIG_HOME:-$HOME/.config}/Devin/User/`.
Use `--data-dir`/`--config-dir` where the tool documents them for non-default locations.

## Environment notes

- Delegated runtime is optional; this guide installs local tooling only.
- Linux can use additional compute or Linux-compatible delegated tooling when available.
- macOS is planned but not claimed as tested.

## Linux specifics

- **Installer choice:** `uv tool install` is the recommended path (isolated environment, managed Python). `pipx install` works identically for PyPI packages; `pip install --user` is the last-resort fallback, no isolation, watch dependency conflicts.
- **PATH:** executables land in `~/.local/bin`. If a command is not found, add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc`/`~/.zshrc` and open a new shell.
- **Distros:** tested on Ubuntu; Debian, Fedora and Arch follow the same steps, only `uv`/Python acquisition differs (distro package or the uv installer script).
- **Headless and minimal environments:** no display is needed, every CLI is text-only. In containers or WSL, install `uv` and Git and follow the same steps; `XDG_*` paths resolve normally.
- **Permissions:** tools read Devin data under `$XDG_DATA_HOME/devin` and write only their own config/state, no root or sudo is required.
- **Scheduling:** optional recurring work belongs to `systemd --user` timers or cron; installation never creates jobs.

## Recurring runs (optional)

_Long-running daemon. Prefer a `systemd --user` service (`WorkingDirectory=<CAMINHO_DO_CHECKOUT>`, `Restart=on-failure`) over `@reboot`._

```cron
@reboot python <CAMINHO_DO_CHECKOUT>/daemon.py --port 8788
```

Equivalent `systemd --user` timer works too; enable lingering if it must run without a login session.


## Monitoring caveat: `Restart=` crash-loops are invisible to `--failed`

A unit with `Restart=always|on-failure` that fails on every start never
reaches the `failed` state: it sits in `activating (auto-restart)`
forever. `systemctl --failed` prints clean while a service is in a
thousands-deep crash loop (real incident: probe dead ~21h, restart
counter >7000, after its `WorkingDirectory` was moved away in a reorg).

To find crash-looping units:

```bash
systemctl --user list-units --state=activating
systemctl --user show devin-office-probe -p NRestarts -p ActiveState
```

The durable guard is not a state listing but an **external freshness
check**: the hub exposes `probe.lastIngestAgoS` in `GET /api/state`, and
an independent cron job alerts when the producer stops landing data.
Producer liveness must never be reported by the producer. A dead probe
cannot tell you it is dead.


## Troubleshooting

- If a command is not found, ensure the `uv` tools directory is on `PATH` and run `uv tool update-shell`.
