# Changelog

## [Unreleased]

## [0.1.1] - 2026-10-15

### Security

- Declared `capabilities.untrustedWorkspaces` (`supported: "limited"`)
  with `restrictedConfigurations` covering `devinBridge.executable` and
  `devinBridge.checkOnStartup`; the startup check now also requires
  `vscode.workspace.isTrusted`, so a user-level `checkOnStartup` can no
  longer spawn the CLI in an untrusted folder.

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
- Windows support without a shell: the npm `.cmd` shim is resolved via
  `where.exe` and the CLI entry (`bin/devin-bridge.js` under the npm
  prefix) runs on the editor's own Node — `execFile`-only, so a target
  like `x & calc` can never be reinterpreted by `cmd`. Fail-closed:
  unresolvable shims surface the install hint instead of a shell
  fallback.
- `devin-bridge-cli` pin (`>=0.1.0,<0.2.0`) in package.json, kept in
  sync with `COMPATIBLE_CLI` and `packages/bridge/package.json` by
  cli-contract.test.js.
- Settings: `devinBridge.executable`, `devinBridge.checkOnStartup`.
