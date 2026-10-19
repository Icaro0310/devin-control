# devin-office, Personal Windows guide

This guide covers unrestricted Windows setup. For restricted machines, see [README.corporate-windows.md](README.corporate-windows.md); for features, shared commands, limitations, and the safety model, see [README.md](README.md).

Personal Windows uses the extended runtime: local execution plus optional Devin VM/QwenPaw delegation when this artifact supports it.

## Prerequisites

- Python 3.10 or newer.
- Git for a source checkout.
- Devin Desktop or CLI on the machine whose sessions you want to view.

## Install

From a repository checkout, install the package and run the standalone
read-only dashboard:

```powershell
cd devin-control\packages\office
pip install -e .          # or: uv tool install . / pipx install .
devin-office --port 8788
```

No install also works — the module self-bootstraps its imports:

```powershell
py -3 src\devin_office\daemon.py --port 8788
```

Open `http://localhost:8788`; the process stops with Ctrl+C.

## Devin paths

Session data normally lives under `%APPDATA%\devin\cli\`; UI state and ACP stores under `%APPDATA%\Devin\User\`.
Use `--data-dir`/`--config-dir` where the tool documents them for non-default locations.

## Environment notes

- Delegated runtime is optional; this guide installs local tooling only.
- Corporate Windows is a separate local-only environment.
- macOS is planned but not claimed as tested.

## Personal Windows specifics

- **Python:** `uv` manages its own Python, which also avoids the Microsoft Store `python.exe` alias stub (it opens the Store instead of running). If you install Python from python.org anyway, tick "Add python.exe to PATH".
- **Shell:** PowerShell 7 + Windows Terminal is the recommended setup; every command also works in `cmd.exe` and Windows PowerShell 5.1, none require admin.
- **Install location:** executables live under `%USERPROFILE%\.local\bin`; data under `%APPDATA%\devin`. Nothing touches `Program Files` or the registry.
- **WSL:** treat it as a Linux machine, follow [README.linux.md](README.linux.md) inside it.
- **Uninstall:** `uv tool uninstall <package>` (or `npm uninstall -g` for a Node.js tool) removes the CLI; delete `%APPDATA%\devin` to remove local data. No services or scheduled tasks are left behind.

## Recurring runs (optional)

_Long-running daemon. `schtasks` cannot set "Start in", so either call `daemon.py` by absolute path as below (installed users can put `devin-office --port 8788` in the `.cmd` instead — the wrapper already prepends `%USERPROFILE%\.local\bin` to `PATH`) or open the task in Task Scheduler afterwards and set Actions → Edit → "Start in" to the checkout directory._

Quoted path arguments inside `/tr` fail differently in cmd.exe and PowerShell 5.1, so this job goes through a wrapper `.cmd` file in your own profile directory (quoting inside the file works normally, and no other user can replace it). The task inherits your user `PATH`, so the wrapper first prepends the standard tool directory (`%USERPROFILE%\.local\bin`); if the tool lives elsewhere, write its full path inside the `.cmd`. Register from PowerShell with the `ScheduledTask` cmdlets, which take the path literally and replace cleanly on rerun (`-Force`):

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\devin-tasks"   # cmd.exe: mkdir "%USERPROFILE%\devin-tasks"
```

Save this as `%USERPROFILE%\devin-tasks\devin-office.cmd`:

```batch
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
py "<CAMINHO_DO_CHECKOUT>\src\devin_office\daemon.py" --port 8788
```

```powershell
$action  = New-ScheduledTaskAction -Execute "$env:USERPROFILE\devin-tasks\devin-office.cmd"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
Register-ScheduledTask -TaskName "devin-office" -Action $action -Trigger $trigger -Force
```

_cmd.exe users: the same registration works from Task Scheduler's GUI (Action = the .cmd path)._

Runs under your account, no admin needed. Adjust the `New-ScheduledTaskTrigger` arguments (or `-AtLogOn` for daemons) to taste.


## Troubleshooting

- Start the daemon from the repository directory with the OS-specific Python launcher shown above.
