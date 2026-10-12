# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Daemon adapters for the dashboard (`adapters/daemon/`):
  `devin-office.service`, a user-level systemd unit keeping the resident
  `daemon.py` server up (`Restart=on-failure`, loopback-confined,
  `ReadWritePaths` covering `session_locks` for the flock probe and the
  checkout for `kanban.json`), and a Windows `task.xml` +
  `install-task.ps1`/`remove-task.ps1` pair (at-logon, no execution-time
  cap; the installer resolves and XML-escapes the checkout path into the
  action). `verify.sh` + `tests/test_daemon_adapter.py` cover the
  artifacts.

### Changed

- Docs: job agendado usa wrapper `.cmd` em `%USERPROFILE%\devin-tasks` + `Register-ScheduledTask -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"`; o wrapper antecede `%USERPROFILE%\.local\bin` ao `PATH` e o registro usa `-Force`.

- README gains the generated `Part of the DEVIN ecosystem` block
  (track/nature/audience/interface rendered from the registry).

- `labeler.yml` is now a thin caller of the shared reusable workflow in `devin-powerups` (`@v1`); PR labeling behavior is unchanged.

- README documents the project as source-only via a generated `DIST-STATUS` banner (no package-registry release; run from a checkout).

### Added

- Session kanban page (`/kanban.html`, served by daemon and hub):
  four columns — Running (lock held + open turn), Blocked (pending
  permission, question tail, or unanswered user message), Review (turn
  ended, awaiting review) and Closed (persisted in `kanban.json` via
  `POST /api/kanban` on whichever side serves `/api/state`). The header
  label `devin-office` links to the kanban; the circuit board is
  unchanged. Collector: `sessmon.py` (`collect_sessions()`), wired into
  `collect_state()` so split mode (probe→hub) carries it for free.
- SwapFile Queue panel (Linux): `swapmon.py` collects swap totals,
  page-in/out rates and the per-process `VmSwap` queue from `/proc`
  read-only; the dashboard renders a bottom-right panel that flags
  OOM-immune processes (`oom_score_adj <= -900`) and hides on hosts
  without `/proc`.

### Fixed

- `session_locked()` on Linux never detected open sessions: POSIX locks
  are advisory, so the `O_RDWR` probe always succeeded. Now uses a
  non-blocking `flock` probe on POSIX (Windows behaviour unchanged).

- Scheduled PM2 jobs (`cron_restart`, idle between runs) no longer render
  as down — they show a ⏰ marker and count as healthy.

- README quick-start path corrected after the monorepo absorption, and the
  per-OS launch commands moved out of the shared guide — they live in
  `README.linux.md` / `README.windows.md` per the platform-docs rule.

## [0.1.0] - 2026-10-03

### Added

- Initial public release of Devin Office, a local-first dashboard of active
  Devin CLI/Desktop sessions and subagents rendered as an SVG circuit view.
- `daemon.py`: standalone read-only mode; reads the local `sessions.db` and
  serves `/api/state` plus the dashboard on loopback, Python stdlib only.
- `probe.py` + `hub.py`: optional split mode; the probe polls local state
  and posts changes to a private hub that serves the dashboard.
- `executor.py`: optional ACP control process for message/spawn/kill
  requests; disabled by default and gated behind explicit opt-in.
- `index.html`: self-contained SVG dashboard (Devin chip with live traces
  to tools and subagent arms); no JavaScript/CSS build step.
- Cross-platform Devin store detection (Windows `%APPDATA%`, Linux XDG,
  macOS), loopback-restricted CORS, and token requirements for
  non-loopback hub binds.
- EN/PT-BR READMEs, tests workflow, preview screenshot and live demo page.
