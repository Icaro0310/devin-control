# devin-bridge for VS Code

Read-only views of [devin-bridge](https://github.com/Icaro0310/devin-control/tree/main/packages/bridge),
the policy-gated ACP bridge that drives `devin acp`: session mappings,
the effective permission policy, and the no-session connectivity probe.

Thin adapter: the extension shells out to the `devin-bridge` CLI and
renders what it returns — no ACP, session or policy logic is reimplemented
here.

**Read-only by construction.** Every CLI call goes through one funnel and
the only argument arrays it can ever receive are whitelisted literals:
`sessions --json`, `policy --show`, `policy --check <cap> <target>` and
`probe --no-session`. Session control — `new`, `resume`, `prompt`, mailbox
`intake`, `policy --init`, or a probe that creates a session — is
unreachable from the editor. Orchestration mutation stays on the real CLI.
`test/cli-contract.test.js` asserts no quoted literal that could reach a
mutating code path exists in the source.

## Requirements

`devin-bridge` installed and on `PATH`:

```sh
npm i -g @icaro0310/devin-bridge
```

Compatible CLI range: `>=0.1.0,<0.2.0` (pinned as `devin-bridge-cli` in
`package.json`; `test/cli-contract.test.js` keeps the pin, the
`COMPATIBLE_CLI` bounds and `packages/bridge/package.json` in sync).

Open a workspace folder — `devin-bridge` resolves `.sessions.json` and
`policy.json` relative to the repo root, so the view matches the CLI run in
the same directory.

## Commands

| Command | What it runs |
| --- | --- |
| `devin-bridge: List sessions` | `devin-bridge sessions --json` → status bar count + JSON document |
| `devin-bridge: Show effective policy` | `devin-bridge policy --show` → JSON document |
| `devin-bridge: Check policy decision` | `devin-bridge policy --check <cap> -- <target>` → decision toast |
| `devin-bridge: Probe ACP connectivity` | `devin-bridge probe --no-session` → status bar verdict + JSON report |

Note: the `--` end-of-options marker lets option-shaped targets (e.g.
`--version`) reach the check literally. Windows launch details live in
[README.windows.md](README.windows.md).

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `devinBridge.executable` | `devin-bridge` | Executable name or absolute path |
| `devinBridge.checkOnStartup` | `false` | List sessions when VS Code starts |

## Development

```sh
npm test        # node --test (unit + read-only/pin contract tests)
```
