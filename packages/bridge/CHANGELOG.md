# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-16

### Added

- Configurable ACP agent resolver (`src/acp-client.js`): the agent
  executable, spawn argv and credentials path are now selected through a
  named strategy registry (`AGENT_STRATEGIES`) plus env overrides —
  `BRIDGE_AGENT` (strategy name), `BRIDGE_AGENT_BIN` (explicit executable,
  the generic escape hatch for any ACP-compatible CLI),
  `BRIDGE_AGENT_ARGS` (JSON array argv override),
  `BRIDGE_AGENT_AUTH` (`authenticate` methodId override; `none` skips
  it, and an agent-advertised `authMethods` list is honoured — methods
  the agent does not offer are never sent) and
  `BRIDGE_CREDENTIALS_PATH` (credentials.toml override).
  `DEVIN_CREDENTIALS_PATH`/`DEVIN_CLI_PATH` keep working as back-compat
  fallbacks; the `devin` strategy and the default resolution order are
  unchanged.

### Security

- The resolved credentials file is pinned into the policy's fs-read deny
  set whatever the rules allow — a custom `BRIDGE_CREDENTIALS_PATH` can
  sit outside the built-in credential-store basenames and still cannot
  be read back by an agent.
- fs path checks now test the canonical `realpath` of every request
  alongside the lexical path: a symlink (or a symlinked directory in the
  configured credentials path) can no longer smuggle a denied target —
  credential stores included — past an `allow` policy.

### Fixed

- `BRIDGE_AGENT_ARGS` set to an empty or whitespace-only string read as
  "unset" and silently spawned the agent with the strategy default
  (`["acp"]` for devin) — a config asking for zero args got a
  subcommand instead. It is now an explicit, actionable error; `'[]'`
  is the only spelling of zero args and unset still defaults.
- A spawn-level failure of the agent process (the ChildProcess `error`
  event — e.g. ENOENT on a bad shebang or EACCES after resolution)
  crashed the whole bridge with `Unhandled 'error' event` instead of
  rejecting the in-flight call. `start()` now attaches an `error`
  listener that rejects pending calls exactly like `exit` does.
- Docs: `readSessionToken` and the README now state the real tokenKey
  behavior — the credentials.toml key name always comes from the
  selected strategy (`windsurf_api_key` for devin); there is no
  `BRIDGE_AGENT_TOKEN_KEY` override, so a custom agent driven purely by
  env must present that key name (or skip auth with
  `BRIDGE_AGENT_AUTH=none`).

## [0.1.1] - 2026-10-15

### Added

- Daemon adapters for the mailbox intake (`adapters/daemon/`):
  `devin-bridge-intake.{service,path,timer}` user-level systemd units —
  a `*.json` glob watch on the inbox plus a polling fallback around the
  same `devin-bridge intake` CLI — and a Windows `task.xml` +
  `install-task.ps1`/`remove-task.ps1` pair (logon trigger + 5-minute
  repetition). The service auto-creates its state/config roots
  (`StateDirectory`/`ConfigurationDirectory`) and marks the remaining
  Devin root optional so a fresh profile can't fail mount setup.
  `verify.sh` + `tests/daemon-units.test.js` cover the artifacts.
- VS Code adapter (`adapters/vscode/`): read-only views of `sessions --json`, `policy --show`, `policy --check`, and `probe --no-session` behind a status bar. Every CLI call passes through a single funnel of whitelisted literal arg arrays — session control (`new`/`resume`/`prompt`/`intake`, `policy --init`, session-creating probes) is unreachable from the editor. Pins `devin-bridge-cli >=0.1.0,<0.2.0`.
- `parseArgs` honors the `--` end-of-options marker: tokens after `--` are positional, so `policy --check <cap> -- <target>` accepts option-shaped targets such as `--version` literally.

### Changed

- README gains the generated `Part of the DEVIN ecosystem` block
  (track/nature/audience/interface rendered from the registry).

- `labeler.yml` is now a thin caller of the shared reusable workflow in `devin-powerups` (`@v1`); PR labeling behavior is unchanged.
- The inline `secrets-scan` job now calls the shared reusable workflow, which switches fixture handling from a blanket `fixtures` directory exclusion to hash-pinned `.secrets-scan-allow` entries.

- Install section now recommends npm `npm install -g @icaro0310/devin-bridge` as the primary route, with `pipx`/source installs documented as alternatives.

- `llms.txt` no longer states a hard-coded ecosystem size; the registry owns the count.

## [0.1.0] - 2026-09-29

### Added

- Node.js ESM package (`devin-bridge`, engines ≥20, zero runtime deps)
  replacing the initial Python scaffold.
- `src/acp-client.js` — ACP client for `devin.exe acp`: NDJSON JSON-RPC
  over stdio, `initialize`/`authenticate` (`_meta.api_key` =
  `windsurf_api_key`, never logged), `session/new`/`load`/`prompt`/
  `cancel`, `session/set_config_option`, streamed text + model label +
  usage/cost extraction, timeout-cancel semantics. Ported from
  `personal-agent-system/gateways/src/devin-acp.js`.
- `src/policy.js` — `policy.json` v1 permission engine: per-capability
  allow/deny/ask for terminal commands, fs read/write paths, network
  hosts; glob matching with deny-wins and fail-closed defaults;
  built-in deny on credential stores; `checkPermissionRequest`
  classifies ACP tool calls by `kind`.
- `src/dispatch.js` — `SessionMap` (atomic `.sessions.json`, ids only),
  `ensureSession` resume-or-create with fallback, `runTask` dispatch.
  Ported from `scripts/devin-repo-task.js`.
- `bin/devin-bridge.js` — CLI: `new`, `resume`, `prompt`, `sessions`,
  `policy --show|--init|--check`; interactive TTY askHandler, `--yes`
  escape hatch, `--sessions-file`/`--policy`/`--bin`/`--timeout-ms`.
- `policy.example.json` (sync-tested), bilingual READMEs, SPEC.md,
  STATUS.md.
- Test suite: 55 `node --test` cases with a scripted fake ACP agent
  fixture over real stdio — no `devin.exe` required.
- CI: Node matrix (Windows + Ubuntu × 22/24) + reusable secrets scan.

### Security

- Default policy is `ask` for every capability — headless runs fail
  closed instead of the original's blind auto-approve.
- `windsurf_api_key`/`credentials.toml` contents are never logged or
  persisted; `.sessions.json` holds ids/cwd/timestamps only.
