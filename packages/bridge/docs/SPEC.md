# SPEC — `devin-bridge`

## 1. Problem

Devin sessions can only be driven interactively: the Desktop TUI and
`devin list` are not scriptable, and `devin -p` refuses to run headless
without `devin auth login` (interactive PKCE). There is no supported way
to spin up an isolated Devin session per repository, feed it prompts and
harvest the result from an external orchestrator — and the community
scripts that do it anyway auto-approve every permission request, which is
unsafe to leave running unattended.

## 2. Devin extra (and the 3 tests)

**Extra:** a *policy-gated* ACP bridge. The session's own capability
requests — terminal commands, filesystem reads/writes, permission
prompts — pass through a per-project `policy.json` (allow/deny/ask)
instead of being blindly auto-approved. Authentication reuses the IDE's
`windsurf_api_key`, so sessions are real, resumable Desktop sessions with
no login flow.

- **Side-by-side:** ACP clients for other agents (Zed's, acp adapters)
  either auto-approve everything or delegate to the agent's own config;
  none gates `devin.exe acp` specifically, nor reuses the IDE session
  token for non-interactive auth.
- **No-Devin:** remove Devin and the whole thing is meaningless — the
  transport is `devin.exe acp`, the auth is Devin's credentials.toml, the
  sessions appear in Devin Desktop.
- **One sentence:** *"Drive Devin from the outside, with a seatbelt."*

## 3. Scope (M1)

- `src/acp-client.js` — NDJSON JSON-RPC client over stdio: `initialize`,
  `authenticate` (`_meta.api_key`), `session/new`, `session/load`,
  `session/prompt`, `session/cancel`, `session/set_config_option`;
  answers agent→client requests (`terminal/*`, `fs/*`,
  `session/request_permission`); collects `session/update` and
  `_cognition.ai/agent_stopped` notifications; extracts usage/cost fields.
- `src/policy.js` — `policy.json` v1: per-capability `allow`/`deny`/`ask`
  for terminal command lines, fs read/write paths and network hosts;
  glob matching, deny-wins, fail-closed defaults; built-in deny on
  credential stores; `checkPermissionRequest` classifies ACP tool calls.
- `src/dispatch.js` — `SessionMap` (`.sessions.json` persistence, atomic
  writes), `ensureSession` (resume-or-create), `runTask` (dispatch).
- `src/labels.js` — `origin:purpose` session labels (BR-4): `parseLabel`
  slug validation, `bridgeStateDir`, `LabelStore` sidecar
  (`session-labels.json`, atomic writes).
- `bin/devin-bridge.js` — CLI: `new`, `resume`, `prompt`, `sessions`,
  `policy`.
- Tests: `node --test`, zero dependencies, fake ACP agent fixture
  (`tests/fixtures/fake-acp.mjs`) over real stdio.

## 4. Non-scope (M1)

- Slack/webhook adapters, daemons, schedulers (M2+).
- Cost reporting dashboards (raw cost fields are surfaced only).
- npm publish.
- MCP server mode; driving agents *other* than `devin.exe acp`.

## 5. Architecture

```
orchestrator script / human
        │  devin-bridge CLI / library
        ▼
  ┌─────────────┐   policy.json (per-repo or global)
  │   Policy    │◄── allow/deny/ask
  └─────┬───────┘
        │ gates terminal/*, fs/*, session/request_permission
  ┌─────▼───────┐  NDJSON JSON-RPC over stdio
  │  DevinAcp   │◄───────────────►  devin.exe acp  (spawned child)
  └─────┬───────┘
        │ sessionId per repo
  ┌─────▼───────┐
  │ SessionMap  │── .sessions.json  {repo: {sessionId, cwd, updatedAt}}
  └─────────────┘
```

- `Policy` never decides from an empty context: `defaults` per capability
  plus explicit allow/deny lists; unmatched → default (shipped: `ask`).
- `DevinAcp` holds no secrets beyond the handshake token (constructor
  option or read fresh from `credentials.toml` each `init()`).
- `SessionMap` values contain only `{sessionId, cwd, updatedAt}`.

## 6. Protocol surface used

| Direction | Method | Purpose |
|---|---|---|
| client→agent | `initialize` | protocolVersion 1 + client capabilities (fs, terminal) |
| client→agent | `authenticate` | `methodId: "devin-browser"`, `_meta.api_key` = windsurf_api_key |
| client→agent | `session/new` | `{cwd, mcpServers: [], _meta?}` → `sessionId`, `configOptions`; `_meta["devin-bridge"]` carries the `{origin, purpose, label}` tag |
| client→agent | `session/load` | resume; fails `session_locked` if open elsewhere |
| client→agent | `session/prompt` | `[{type:"text",text}]` → `stopReason`, `usage` |
| client→agent | `session/cancel` | notification; sent on client-side timeout |
| client→agent | `session/set_config_option` | model switching (`configId: "model"`) |
| agent→client | `terminal/create|output|wait_for_exit|kill|release` | gated by `terminal` rules |
| agent→client | `fs/read_text_file` / `fs/write_text_file` | gated by `fs.read`/`fs.write` rules |
| agent→client | `session/request_permission` | classified by `toolCall.kind`, gated |
| agent→client | `session/update` (notification) | `agent_message_chunk` → streamed text |
| agent→client | `_cognition.ai/agent_stopped` (notification) | `stats.modelLabel`, usage/cost fields |

## 7. `policy.json` schema (version 1)

```jsonc
{
  "version": 1,                      // required; other versions are rejected
  "defaults": {                      // any omitted capability defaults to "ask"
    "terminal": "ask",               // allow | deny | ask
    "fsRead": "ask",
    "fsWrite": "ask",
    "network": "ask",
    "permission": "ask"
  },
  "terminal": {
    "allow": ["git status", "npm test*"],   // whole command line, * = any text
    "deny":  ["rm -rf*", "format *"]
  },
  "fs": {
    "read":  { "allow": ["**"], "deny": ["**/.env", "**/credentials*"] },
    "write": { "allow": ["src/**"], "deny": ["**/.git/**"] }
  },
  "network": {
    "allow": ["api.github.com"],
    "deny":  ["*.onion", "169.254.169.254"]
  }
}
```

Rules:

- **Deny wins.** A target matching any deny pattern is denied even if it
  also matches an allow pattern.
- **Fail closed.** Unmatched targets return the capability default;
  shipped default is `ask`, and a headless `ask` resolves to deny unless
  an `askHandler`/operator answers `allow`.
- **fs patterns** use path globs: `*` within a segment, `**` any depth
  (`**/` also matches zero directories), `?` one char. Relative fs
  patterns are tried against the root-relative path; absolute patterns
  against the resolved path. Matching is case-insensitive on Windows.
- **terminal/network patterns** treat `*` as "any run of characters"
  (command lines and hostnames have no segment semantics).
- **Built-in denies** (`**/credentials.toml`, `**/windsurf_api_key`) are
  appended to `fs.read.deny` unconditionally — an `allow: ["**"]` policy
  still cannot read the credential store the bridge itself authenticates
  with.

`session/request_permission` classification (params.toolCall):

| `kind` | Checked as |
|---|---|
| `execute` | `terminal` on `rawInput.command`/`rawInput.input`/`title` |
| `read`, `search` | `fsRead` on `locations[0].path` |
| `edit`, `write`, `delete`, `move` | `fsWrite` on `locations[0].path` |
| `fetch` | `network` on URL host from `rawInput.url`/`title` |
| other / unresolvable | `defaults.permission` |

## 8. `.sessions.json` contract

```json
{
  "devin-bridge": {
    "sessionId": "uuid-or-name",
    "cwd": "C:/path/to/repo",
    "updatedAt": "2026-09-29T20:00:00.000Z"
  }
}
```

- Keys are repo directory basenames; values are ids, paths and an ISO
  timestamp **only** — never tokens or credentials.
- Written atomically (tmp file + rename) after a session is established.
- Resume semantics: explicit `--resume <id>` > stored id; a failed
  `session/load` (locked, missing, expired) falls back to `session/new`
  and the mapping is rewritten with the live id.

### 8a. Session labels (BR-4)

Sessions the bridge *creates* are labelled `origin:purpose` so
downstream automation can recognise them deterministically.

- `--label origin:purpose` (default `bridge:unlabeled`); each part must
  match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` — slugs only, so a label can
  never carry prompt text or session content.
- `session/new` sends `_meta: {"devin-bridge": {origin, purpose,
  label}}` (the ACP `_meta` extension point). Whether the agent
  persists `_meta` is undocumented — it is best-effort only.
- The authoritative record is the sidecar `session-labels.json` in the
  bridge state dir (`$XDG_STATE_HOME/devin-bridge` on Linux,
  `%LOCALAPPDATA%\devin-bridge` on Windows;
  `DEVIN_BRIDGE_STATE_DIR`/`--state-dir` override):

```json
{
  "version": 1,
  "sessions": {
    "<sessionId>": {
      "label": "janitor:classification",
      "origin": "janitor",
      "purpose": "classification",
      "createdAt": "2026-10-04T00:00:00.000Z",
      "cwd": "/abs/repo"
    }
  }
}
```

- Whitelisted fields only — never prompts, tokens or session content.
- Written atomically; missing/corrupt files load empty.
- Resumed sessions keep their creation label; `session/load` is never
  relabelled.

## 9. Security requirements (enforced in code)

1. Default policy is `ask` for every capability — nothing auto-approves.
2. `windsurf_api_key` and `credentials.toml` contents are never logged,
   never written to `.sessions.json`, never included in errors.
3. Agent-side fs reads of credential stores are denied by built-in rules
   even under permissive user policies.
4. Policy parse errors and unknown `version` fail closed (throw), never
   silently default to permissive.
5. `askHandler` resolves to deny on throw or ambiguous answers.

## 10. CLI reference

```
devin-bridge new <repo-dir> [--model v]      create session, record mapping
devin-bridge resume <repo-dir> [--resume id] reconnect / verify mapping
devin-bridge prompt <repo-dir> <text>        run prompt (or --file f)
devin-bridge sessions [--json]               list repo → sessionId map
devin-bridge policy --show                   effective policy JSON
devin-bridge policy --init [--force]         write ./policy.json example
devin-bridge policy --check <cap> <target>   print one decision

Options: --sessions-file f  --policy f  --bin path  --timeout-ms n  --yes
         --label origin:purpose  --state-dir d
```

- `prompt` streams agent text to stderr live; stdout ends with a RESULT
  JSON block (`repo`, `sessionId`, `resumed`, `model`, `stopReason`,
  `cost`, `timedOut`) plus the aggregated TEXT.
- Interactive TTY: policy `ask` prompts the operator on stderr
  (`[y/N]`, default deny). Non-TTY is fail-closed unless `--yes`.
- Agent resolution is configurable: `BRIDGE_AGENT_BIN` (explicit
  executable for any ACP CLI) → the selected `BRIDGE_AGENT` strategy's
  candidates (`AGENT_STRATEGIES`; no fallback across strategies — the
  default `devin` strategy is `DEVIN_CLI_PATH` →
  `%LOCALAPPDATA%\Programs\Devin\...\devin.exe` → PATH).
  `BRIDGE_AGENT_ARGS` overrides the spawn argv (JSON array, default
  `["acp"]`); `BRIDGE_AGENT_AUTH` overrides the `authenticate`
  methodId (`none` skips it; an agent-advertised `authMethods` list is
  honoured — unsupported methods are not sent);
  `BRIDGE_CREDENTIALS_PATH` overrides the credentials.toml path before
  `DEVIN_CREDENTIALS_PATH`. The resolved credentials path is always
  denied to agent fs reads regardless of policy rules.

## 11. Testing

`node --test` (stdlib, Node ≥20). No devin.exe required:

- `tests/fixtures/fake-acp.mjs` — scripted NDJSON agent spawned as a real
  child process; canned handshake + session lifecycle; prompt triggers
  (`NEED_PERM`, `RUN_TERM`, `READ_FILE`, `WRITE_FILE`, `COST`, `HANG`)
  exercise permission/terminal/fs/timeout paths end-to-end.
- Policy tests cover glob semantics, deny-wins, defaults, schema
  validation, `checkPermissionRequest` classification.
- Dispatch tests cover mapping persistence/atomicity and resume
  fallback against the fixture.
- `policy.example.json` is sync-tested against `examplePolicyDoc()`.
