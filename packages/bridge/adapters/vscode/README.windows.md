# devin-bridge for VS Code — Windows notes

## Launch mechanism

The npm install on Windows exposes a `devin-bridge.cmd` shim, which
`execFile` cannot launch directly — and routing the call through
`cmd.exe` would let a policy-check target containing `&`, `|` or `>`
inject a command. The adapter therefore never uses a shell:

- when `devinBridge.executable` is a `.js` path, it is spawned through
  the editor's own Node (`process.execPath`);
- when it is a bare name (default `devin-bridge`), the shared
  `safe-executable.cjs` resolver walks `PATH` itself — never `where.exe`,
  which searches the cwd first — and unwraps the npm `.cmd` shim to its
  real `.js` entry, which is spawned on `process.execPath`;
- nothing resolves → the "install it" message shows. Fail-closed: no
  shell, ever.

Because Windows `CreateProcess` searches the current directory *before*
`PATH`, a bare `devin-bridge` spawned with `cwd` = the workspace could
run a binary planted inside an untrusted repo. The resolver returns only
absolute spawn targets, and in an untrusted workspace (Restricted Mode)
it refuses any candidate that resolves inside the repo — the workspace
can never supply the executable. The same resolver guards the bridge
CLI's own autodetect of the agent (`BRIDGE_AGENT_BIN`, `DEVIN_CLI_PATH`,
the per-user install dir, then `PATH`): a repo-resident agent binary is
never picked up.

`policy --check` targets always travel as one literal argv element after
a `--` end-of-options marker.

## Requirements

Same as the main README: `npm i -g @icaro0310/devin-bridge` and an open
workspace folder so `.sessions.json`/`policy.json` resolve at the repo
root.
