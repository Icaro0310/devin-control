<div align="center">

<img src="assets/banner.svg" alt="devin-bridge" width="100%"/>

<a href="https://github.com/Icaro0310/devin-control/actions/workflows/test-bridge.yml"><img src="https://github.com/Icaro0310/devin-control/actions/workflows/test-bridge.yml/badge.svg" alt="tests"/></a>


<a href="https://github.com/Icaro0310/devin-control/actions/workflows/test-bridge.yml"><img src="https://github.com/Icaro0310/devin-control/actions/workflows/test-bridge.yml/badge.svg" alt="ci"/></a>
<a href="https://scorecard.dev/viewer/?uri=github.com/Icaro0310/devin-control"><img src="https://api.scorecard.dev/projects/github.com/Icaro0310/devin-control/badge" alt="OpenSSF Scorecard"/></a>
<a href="https://deepwiki.com/Icaro0310/devin-control"><img src="https://deepwiki.com/badge.svg" alt="DeepWiki"/></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"/></a>
<a href="https://nodejs.org/"><img src="https://img.shields.io/badge/node-20%2B-blue" alt="Node 20+"/></a>
<a href="https://github.com/Icaro0310/devin-control"><img src="https://img.shields.io/github/stars/Icaro0310/devin-control" alt="GitHub stars"/></a>
<a href="https://github.com/Icaro0310/devin-control/commits/main"><img src="https://img.shields.io/github/last-commit/Icaro0310/devin-control" alt="Last commit"/></a>
<a href="https://github.com/Icaro0310/awesome-devin"><img src="https://img.shields.io/badge/part%20of-devin--*-ecosystem-7c3aed" alt="devin-* ecosystem"/></a>
<a href="https://github.com/Icaro0310/devin-control/issues"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs welcome"/></a>
</div>

<!-- DEVIN-ECO:BEGIN -->
> **Part of the [DEVIN ecosystem](https://github.com/Icaro0310/awesome-devin)**  
> Track: Control · Nature: product  
> For: Security engineers, AI engineers  
> Interface: CLI / Bridge
<!-- DEVIN-ECO:END -->


# devin-bridge

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.

**[Linux](README.linux.md)** · **[Personal Windows](README.windows.md)** · **[Corporate Windows](README.corporate-windows.md)**

Part of the [awesome-devin](https://github.com/Icaro0310/awesome-devin) ecosystem: the curated hub for the devin-* tools.

A policy-gated bridge that drives `devin.exe acp` from the outside:
create/resume isolated Devin sessions per project, send prompts, stream
results — with a `policy.json` deciding what the session may do.

## The problem

Devin Desktop sessions are not scriptable. `devin -p` requires an
interactive `devin auth login`, `devin list` is a TUI, and the
community snippets that drive `devin.exe acp` directly **auto-approve
every permission request** — fine for a demo, dangerous to leave running
against a real repo overnight.

## Prior art

This tool speaks ACP over newline-delimited JSON-RPC, manages isolated Devin
sessions per project, and adds a permission gate for terminal, filesystem and
permission requests. See [docs/SPEC.md](docs/SPEC.md) for the protocol and
policy contract.

## What makes it Devin-native

- **Side-by-side:** generic ACP clients auto-approve or delegate to the
  agent's config; this one gates `terminal/*`, `fs/*` and
  `session/request_permission` through a per-repo `policy.json`
  (allow/deny/ask, deny-wins, fail-closed by default).
- **No-Devin:** pointless without it — transport is `devin.exe acp`,
  auth is the IDE's `windsurf_api_key` from `credentials.toml` (no PKCE
  login), sessions show up grouped by repo in Devin Desktop.
- **One sentence:** *drive Devin from the outside, with a seatbelt.*

## Install

Requires Node.js ≥ 20, Git, and an authenticated Devin CLI/Desktop install.

```bash
npm install -g @icaro0310/devin-bridge
```

From source (development):

```bash
git clone https://github.com/Icaro0310/devin-control.git
cd devin-bridge
npm install -g .
```

The bridge reads `windsurf_api_key` from `%APPDATA%\\devin\\credentials.toml`
on Windows and `$XDG_DATA_HOME/devin/credentials.toml` on Linux (normally
`~/.local/share/devin/credentials.toml`). Override with `BRIDGE_CREDENTIALS_PATH`
(`DEVIN_CREDENTIALS_PATH` still works); the Devin CLI is resolved from `PATH`,
`DEVIN_CLI_PATH` or `BRIDGE_AGENT_BIN`. The token is never logged or persisted.

### Choosing the agent (`BRIDGE_AGENT*`)

The bridge speaks generic ACP; the Devin CLI is just the default agent.
Everything per-agent lives in a strategy registry (`AGENT_STRATEGIES` in
`src/acp-client.js`) plus env overrides:

| Env | Meaning |
| --- | --- |
| `BRIDGE_AGENT` | strategy name from the registry (default `devin`; unknown names fail fast) |
| `BRIDGE_AGENT_BIN` | explicit agent executable — the generic escape hatch for any ACP CLI |
| `BRIDGE_AGENT_ARGS` | JSON array argv override (default: the strategy's, `["acp"]` for devin); `'[]'` = zero args. Setting it empty or whitespace-only is an error — unset it for the default |
| `BRIDGE_AGENT_AUTH` | `authenticate` methodId override; `none` skips the call (default: the strategy's, `devin-browser` for devin) |
| `BRIDGE_CREDENTIALS_PATH` | credentials.toml override for any strategy |

Resolution order — executable: `BRIDGE_AGENT_BIN` → the selected
strategy's candidates (`BRIDGE_AGENT`, default `devin`:
`DEVIN_CLI_PATH` → install dir → `devin` on `PATH`). A selected strategy
is used as-is — there is no fallback across strategies. Credentials:
`BRIDGE_CREDENTIALS_PATH` → `DEVIN_CREDENTIALS_PATH` → the strategy's
platform default. `--bin` beats all env config. To drive a non-Devin ACP
CLI without touching code:

```bash
export BRIDGE_AGENT_BIN=/opt/my-agent/bin/cli
export BRIDGE_AGENT_ARGS='["--acp"]'        # argv that enters ACP mode
export BRIDGE_AGENT_AUTH=none               # skip authenticate, or a methodId it advertises
export BRIDGE_CREDENTIALS_PATH=/etc/my-agent/credentials.toml
```

Auth: `init` sends one `authenticate` call with `_meta.api_key` from the
session token (`--token`/credentials file). When the agent advertises
`authMethods`, only an advertised method is sent — a configured method
it does not offer is skipped, and `BRIDGE_AGENT_AUTH=none` disables the
call for agents that need no handshake. Agents requiring interactive or
multi-step auth flows are out of scope; the token still comes from the
strategy's credentials file (`tokenKey`). There is no env override for
the key name itself (`BRIDGE_AGENT_TOKEN_KEY` does not exist): a custom
agent driven purely by env still reads `windsurf_api_key = "..."` — the
`devin` strategy's key — from its credentials.toml. For agents whose
secrets live under a different key, add a registry entry, or use
`BRIDGE_AGENT_AUTH=none` to skip `authenticate` entirely.

To support one natively (own install-path candidates, credential
location, default argv, auth method), add a named entry to
`AGENT_STRATEGIES`.

## Usage

The examples below use a policy file in the current directory. `new` and
`prompt` store a repo-to-session mapping in `.sessions.json`; keep that local
file out of commits because it contains session IDs and local paths.

**Windows (PowerShell):**

```powershell
devin-bridge policy --init
devin-bridge new "C:\src\my-repo"
devin-bridge prompt "C:\src\my-repo" "run the test suite and fix failures"
devin-bridge prompt "C:\src\my-repo" --file docs\KICKOFF.md
devin-bridge sessions
devin-bridge policy --check terminal "rm -rf /"
```

**Linux:**

```bash
devin-bridge policy --init
devin-bridge new "$HOME/src/my-repo"
devin-bridge prompt "$HOME/src/my-repo" "run the test suite and fix failures"
devin-bridge prompt "$HOME/src/my-repo" --file docs/KICKOFF.md
devin-bridge sessions
devin-bridge policy --check terminal "rm -rf /"
```

Policy `ask` decisions prompt the operator interactively on a TTY;
non-interactive runs stay fail-closed unless `--yes` is passed.

### Session labels (`--label`)

Every session the bridge *creates* is tagged so downstream automation
(devin-janitor classification, devin-dream scorekeeping) can recognise
bridge-created sessions deterministically:

```bash
devin-bridge new "$HOME/src/my-repo" --label janitor:classification
devin-bridge prompt "$HOME/src/my-repo" "..." --label dream:scorekeeping
```

- Format is `origin:purpose` (default `bridge:unlabeled`). Each part is
  a slug (`[A-Za-z0-9._-]`, ≤64 chars) — a label can never carry prompt
  text or session content.
- The label is sent to the agent under `session/new` `_meta`
  (`{"devin-bridge": {origin, purpose, label}}`), the spec-sanctioned ACP
  extension point. Whether Devin persists `_meta` is undocumented.
- The **authoritative record** is a local sidecar
  `session-labels.json` mapping `sessionId → {label, origin, purpose,
  createdAt, cwd}`, written atomically in the bridge state dir:
  `$XDG_STATE_HOME/devin-bridge` (Linux), `%LOCALAPPDATA%\devin-bridge`
  (Windows). Override with `DEVIN_BRIDGE_STATE_DIR` or `--state-dir`.
- Resumed sessions keep the label they were created with; the bridge
  never relabels sessions it did not create. `devin-bridge sessions`
  shows the recorded label per session.

Named presets (`--preset`, or `policy --init --preset <name>`):

- `ask` — the shipped fail-closed default; everything requires approval.
- `read-only` — denies terminal/fsWrite/network; reads stay open except
  credential files. For intake and automation that must observe, not act.
- `full` — allows everything. **High risk** — trusted scratch environments
  only; the CLI warns every time it is selected.

## Works with Devin alone (Devin-only mode)

devin-bridge *is* the Devin-only path: it drives `devin acp` directly using
the `credentials.toml` the Devin CLI already stores — no second runtime, no
message broker, no extra API key. Requirements are just Node.js >= 20 and a
signed-in Devin CLI (`devin` on PATH, `DEVIN_CLI_PATH` or `BRIDGE_AGENT_BIN`). The permission
policy is fail-closed by default: anything not explicitly allowed is denied,
and credentials are never logged or persisted.


### `probe` — ACP compatibility probe (BR-1)

`devin-bridge probe` checks the ACP handshake end to end: `initialize` +
`authenticate`, then (unless `--no-session`) a `session/new` in a temp dir
labelled `bridge:probe` — which `devin-janitor` can later reap as an
automatic session. Output is a JSON compat report (checks, models,
configOptions); exit code 1 on failure. This is the foundation for
`devin-internals-spec`'s drift checks.

### `intake` — file-based mailbox (BR-3)

`devin-bridge intake` processes task files dropped in
`<state-dir>/mailbox/inbox/*.json` — **no network listener**. A task is
`{"task": "prompt text", "repo"?: "<dir>", "model"?: "<id>"}`.

- Tasks are **untrusted input**: they only become a prompt inside a normal
  session, so the active policy still gates every capability (default
  `ask` = human approves each step).
- FIFO by filename; each file moves inbox → processing → done/failed, with
  `.result.json` / `.err` sidecars.
- `inbox/` must be owner-only (`chmod 700`); files over 64KB and tasks over
  16k chars are rejected.
- `--dry-run` validates without creating sessions; `--repo` sets a default
  cwd for tasks without `repo`.

## Limitations

- Uses the **undocumented** `acp` mode and `_meta.api_key` authentication
  of the Devin CLI; both may change without notice (tested against Devin CLI
  3000.10.x).
- Windows and Linux are supported. The executable and credentials file are
  resolved per OS; use `BRIDGE_AGENT_BIN`/`DEVIN_CLI_PATH` and
  `BRIDGE_CREDENTIALS_PATH`/`DEVIN_CREDENTIALS_PATH` to override.
- `session/load` cannot steal a session open elsewhere — it fails
  `session_locked` and the dispatcher falls back to `session/new`.
- Does not expose agent-side MCP servers (`mcpServers: []` is sent).

## Development

```bash
npm test        # node --test — stdlib runner, zero deps
```

Tests use a scripted fake ACP agent (`tests/fixtures/fake-acp.mjs`) over
real stdio — no Devin CLI binary or credentials are needed.

## When to use this

- You want to script Devin sessions per repo — create, resume, prompt — from outside the interactive UI.
- You want a permission gate: `policy.json` allows/denies/asks per tool kind, deny-wins, fail-closed by default.
- You want to leave an agent loop running without auto-approving every terminal and filesystem request.
- You want to test what a policy would decide first: `devin-bridge policy --check terminal "<cmd>"`.

## When NOT to use this

- You need agent-side MCP servers — the bridge sends `mcpServers: []`.
- You cannot tolerate undocumented internals: it uses the CLI's `acp` mode and `_meta.api_key` auth, which may change without notice.
- You want to take over a session already open elsewhere — `session/load` fails `session_locked`.

## FAQ

**How do I script Devin sessions without auto-approving every permission?** Run `devin-bridge policy --init`, then `devin-bridge new "<repo>"` and `devin-bridge prompt "<repo>" "..."`. Every `terminal/*`, `fs/*` and permission request passes through your `policy.json` — anything not explicitly allowed is denied, and `ask` decisions prompt interactively only on a TTY.

**What does devin-bridge need to authenticate?** Nothing extra. It reads `windsurf_api_key` from the Devin CLI's existing `credentials.toml` (`%APPDATA%\devin\` on Windows, `$XDG_DATA_HOME/devin/` on Linux) and resolves the CLI from `PATH`, `DEVIN_CLI_PATH` or `BRIDGE_AGENT_BIN`. The token is never logged or persisted.

**Is devin-bridge safe to run unattended?** Safer than auto-approving snippets, with caveats. The default is fail-closed: unknown requests are denied, and non-interactive runs stay closed unless `--yes` is passed. It still depends on undocumented `acp` mode internals, so pin a tested Devin CLI version.

## License

MIT — see [LICENSE](LICENSE).


---

If this saved you debugging time, a ⭐ on the repo helps others find it.
