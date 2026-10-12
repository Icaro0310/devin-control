# devin-bridge for VS Code — Windows notes

## Launch mechanism

The npm install on Windows exposes a `devin-bridge.cmd` shim, which
`execFile` cannot launch directly — and routing the call through
`cmd.exe` would let a policy-check target containing `&`, `|` or `>`
inject a command. The adapter therefore never uses a shell:

- when `devinBridge.executable` is a `.js` path, it is spawned through
  the editor's own Node (`process.execPath`);
- when it is a bare name (default `devin-bridge`), the adapter resolves
  the shim via `where.exe`, then runs Node on the npm-generated entry at
  `<shim-dir>\node_modules\@icaro0310\devin-bridge\bin\devin-bridge.js`;
- nothing resolves → the spawn fails `ENOENT` and the "install it"
  message shows. Fail-closed: no shell, ever.

`policy --check` targets always travel as one literal argv element after
a `--` end-of-options marker.

## Requirements

Same as the main README: `npm i -g @icaro0310/devin-bridge` and an open
workspace folder so `.sessions.json`/`policy.json` resolve at the repo
root.
