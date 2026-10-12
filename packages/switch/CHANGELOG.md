# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Daemon adapters for the drift check (`adapters/daemon/`):
  `devin-switch-drift.{service,timer}` user-level systemd units (daily
  `devin-switch doctor` with `Persistent=true` catch-up, notification
  only) and a Windows `task.xml` + `install-task.ps1`/`remove-task.ps1`
  pair (logon + daily, `StartWhenAvailable`; the doctor report appends
  to `%LOCALAPPDATA%\devin-switch\drift.log` so it survives the task's
  discarded console output). `verify.sh` +
  `tests/test_daemon_adapter.py` cover the artifacts.

### Changed

- README gains the generated `Part of the DEVIN ecosystem` block
  (track/nature/audience/interface rendered from the registry).

- `labeler.yml` is now a thin caller of the shared reusable workflow in `devin-powerups` (`@v1`); PR labeling behavior is unchanged.

- README install section replaced by a generated `DIST-STATUS` banner stating the tool is source-only (no PyPI release yet) and offering both `pipx` and `uv` source installs.

### Added

- `devin-switch export <profile> [--out FILE]` — serializes a profile to
  a portable JSON archive (`devin-switch/profile-archive`, schema
  version 1: per-file base64 bytes + sha256, `profile.json` metadata as
  fields). Writes to stdout by default; `--out` never overwrites an
  existing file. `credentials.toml` is excluded without being read;
  credential-named files trigger a stderr warning.
- `devin-switch import <file> [--name NAME] [--force]` — dry-run by
  default; `--apply` validates the archive fail-closed (format and
  schema version, JSON shape, base64, per-file sha256, managed-path
  rules — no `..`/absolute/backslash traversal) and materializes
  `profiles/<name>/` through a staging dir + rename. Refuses to replace
  an existing profile without `--force`; appends an `import` journal
  entry.

- `profiles/lab/` — G3 A/B evaluation overlay: pinned model
  (`SWE-2-High` — the free-tier model as of 2026-10-04),
  explicitly empty `hooks` and `mcpServers` (no learning-loop or
  prompt-logging hooks, no memory MCP, `autoGenerateMemories` off), and
  the serial-vs-isolated config-dir caveat documented in
  `profiles/README.md`. Mirrored in `src/devin_switch/profiles/` for
  pipx installs.

## [0.1.0] - 2026-10-04

### Added

- `devin-switch list` / `show <profile>` / `diff <a> <b>` — read-only
  profile inspection with values masked.
- `devin-switch use <profile>` — dry-run by default (masked per-file plan);
  `--apply` snapshots every file about to change into
  `.devin-ecosystem/switch-backups/<ts>/`, verifies sha256 of each copy
  against the manifest *and* the live source, then writes atomically
  (tmp + rename) and appends to `.devin-ecosystem/switch-journal.jsonl`.
- `devin-switch rollback` — dry-run by default; `--apply` restores the
  newest `use` backup (restores modified files, deletes created ones)
  and journals the rollback.
- `devin-switch doctor` — JSONC/hooks/MCP shape checks over both config
  roots (same documented shapes as devin-doctor, implemented inline) plus
  closest-profile ranking; exit 1 on any FAIL.
- `profiles/` example overlays: `corporate` (locked-down: remote MCP
  gateway, conservative cascade, audit hook) and `personal` (local stdio
  MCP servers, permissive cascade, session hooks), mirrored inside the
  package for pipx installs.
- Secret masking in all output: credential-named files withhold contents;
  sensitive key paths and token-looking values render as `<redacted>`.
- `credentials.toml` is never managed — not read, not written, ever.
- Test suite (62 tests) running entirely on synthetic tmp-dir roots —
  use→rollback round-trip, journal integrity, atomic-write leftovers,
  and end-to-end assertions that no literal secret reaches CLI output.
