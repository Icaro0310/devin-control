# Changelog

## [0.1.0]

### Added

- Read-only views of the devin-bridge ACP bridge: `sessions --json` into
  a status-bar count + JSON document, `policy --show` document,
  `policy --check <cap> <target>` decision toast, and
  `probe --no-session` connectivity verdict + JSON report.
- Read-only by construction: every CLI call passes through one funnel
  fed by whitelisted literal arg arrays — `new`/`resume`/`prompt`/
  `intake`, `policy --init` and session-creating probes are unreachable;
  the contract test scans every quoted literal in the source.
- `policy --check` inserts a `--` end-of-options marker before the
  target so option-shaped targets (`--version`, `--json`) reach the
  check literally.
- Windows support: commands launch via `shell: true` on win32 (the npm
  install is a `.cmd` shim); Node quotes each argv element for cmd, so
  user input stays a single argument.
- `devin-bridge-cli` pin (`>=0.1.0,<0.2.0`) in package.json, kept in
  sync with `COMPATIBLE_CLI` and `packages/bridge/package.json` by
  cli-contract.test.js.
- Settings: `devinBridge.executable`, `devinBridge.checkOnStartup`.
