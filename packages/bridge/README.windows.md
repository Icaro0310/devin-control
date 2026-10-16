# devin-control, Personal Windows guide

This guide covers unrestricted Windows setup. For restricted machines, see [README.corporate-windows.md](README.corporate-windows.md); for features, shared commands, limitations, and the safety model, see [README.md](README.md).

Personal Windows uses the extended runtime: local execution plus optional Devin VM/QwenPaw delegation when this artifact supports it.

## Prerequisites

- Node.js 20 or newer and npm.

## Install

Install the Node.js CLI:

```powershell
npm install --global "@icaro0310/devin-bridge"
```

## Devin paths

Session data normally lives under `%APPDATA%\devin\cli\`; UI state and ACP stores under `%APPDATA%\Devin\User\`.
Use `--data-dir`/`--config-dir` where the tool documents them for non-default locations.

## Agent env

The bridge drives `devin.exe acp` by default (`devin.exe` on `PATH`, the per-user install dir, or `DEVIN_CLI_PATH`); env vars select a different ACP-compatible agent or override its locations:

```powershell
$env:BRIDGE_AGENT = "devin"                                   # strategy name (default)
$env:BRIDGE_AGENT_BIN = "C:\tools\my-agent\agent.exe"         # explicit executable
$env:BRIDGE_AGENT_ARGS = '["--acp"]'                          # argv override (JSON array)
$env:BRIDGE_AGENT_AUTH = "none"                               # auth methodId; "none" skips
$env:BRIDGE_CREDENTIALS_PATH = "C:\tools\my-agent\credentials.toml"
```

`DEVIN_CLI_PATH`/`DEVIN_CREDENTIALS_PATH` remain valid legacy overrides. See [README.md](README.md#choosing-the-agent-bridge_agent) for the resolution order.

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

## Troubleshooting

- If the bridge command is missing, check that npm's global executable directory is on `PATH` (`npm config get prefix`).
