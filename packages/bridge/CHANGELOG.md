# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
