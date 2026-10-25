<div align="center">

<a href="https://github.com/Icaro0310/devin-control/actions/workflows/test-switch.yml"><img src="https://github.com/Icaro0310/devin-control/actions/workflows/test-switch.yml/badge.svg" alt="ci"/></a>

<a href="https://scorecard.dev/viewer/?uri=github.com/Icaro0310/devin-control"><img src="https://api.scorecard.dev/projects/github.com/Icaro0310/devin-control/badge" alt="OpenSSF Scorecard"/></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"/></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+"/></a>
<a href="https://github.com/Icaro0310/devin-control"><img src="https://img.shields.io/github/stars/Icaro0310/devin-control" alt="GitHub stars"/></a>
<a href="https://github.com/Icaro0310/devin-control/commits/main"><img src="https://img.shields.io/github/last-commit/Icaro0310/devin-control" alt="Last commit"/></a>
<a href="https://github.com/Icaro0310/awesome-devin"><img src="https://img.shields.io/badge/part%20of-devin--*-ecosystem-7c3aed" alt="devin-* ecosystem"/></a>
<a href="https://github.com/Icaro0310/devin-control/issues"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs welcome"/></a>
</div>

<!-- DEVIN-ECO:BEGIN -->
> **Part of the [DEVIN ecosystem](https://github.com/Icaro0310/awesome-devin)**  
> Track: Control · Nature: product  
> For: Local-first ops, Maintainers, DevOps engineers  
> Interface: CLI  
> Path: DevOps engineers · step 3/3 — after `devin-bridge`
<!-- DEVIN-ECO:END -->

# devin-switch

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.

**[Linux](README.linux.md)** · **[Personal Windows](README.windows.md)** · **[Corporate Windows](README.corporate-windows.md)**

Part of the [awesome-devin](https://github.com/Icaro0310/awesome-devin) ecosystem: the curated hub for the devin-* tools.

`devin-switch` swaps between named **Devin configuration profiles** —
hooks, MCP servers, models, cascade rules, UI settings — with a verified
snapshot before every write and a journal you can roll back from.
**Dry-run by default**: nothing is ever written unless you pass
`--apply`.

## The problem

Devin's behaviour lives in a handful of JSONC files spread across two
roots: `config.json` + `mcp_config.json` under the **data dir**
(`~/.config/devin`, `%APPDATA%/devin`) and `User/settings.json` under the
**UI config dir** (`~/.config/Devin`, `%APPDATA%/Devin`). Wanting a
locked-down "work" setup and a permissive "personal" setup means editing
the same files back and forth — by hand, with no undo.

`devin-switch` treats each variant as a declarative overlay directory and
does the swap safely.

## Safety model

- **Dry-run by default** — `use` and `rollback` print a masked plan and
  write *nothing* (not even the journal) without `--apply`.
- **Verified snapshot** — before any write, every file about to change is
  copied into `.devin-ecosystem/switch-backups/<ts>/` with a sha256
  manifest. The copies are re-hashed *and* compared against the live
  sources; any mismatch aborts before a single byte is written.
- **Atomic writes** — sibling tmp file + `os.replace`; no half-written
  config.
- **`credentials.toml` is never touched** — not read, not written, not
  even opened for a hash. A profile that ships one gets a `skip`.
- **Secrets masked in all output** — credential-named files
  (`.env*`, `*.pem`, `*secret*`, …) never have contents printed; inside
  printable files, values under sensitive key names (`token`, `secret`,
  `password`, `api_key`, `auth`…) or that look like tokens (long alnum
  blobs, JWTs, `sk-`/`ghp_`/`xox` prefixes) render as `<redacted>`.
- **Fail-closed imports** — profile archives are validated end to end
  (schema, per-file sha256, path safety) before anything is written;
  `import` never overwrites an existing profile without `--force`.
- **No network** — stdlib only, Python ≥ 3.10.

## Install

Requires Python ≥ 3.10 and `pipx` or `uv`. Per-OS setup lives in the platform guides: [Linux](README.linux.md) · [Personal Windows](README.windows.md) · [Corporate Windows](README.corporate-windows.md).

## Usage

```bash
devin-switch list                       # discovered profiles
devin-switch show personal              # what a profile manages (keys, not values)
devin-switch diff corporate personal    # masked diff between two profiles
devin-switch use personal               # DRY-RUN: masked per-file plan
devin-switch use personal --apply       # snapshot → verify → write → journal
devin-switch rollback                   # DRY-RUN: what would be restored
devin-switch rollback --apply           # restore the last verified backup
devin-switch export personal            # portable JSON archive to stdout
devin-switch export personal --out personal.json
devin-switch import personal.json       # DRY-RUN: validate + preview only
devin-switch import personal.json --apply            # create profiles/personal/
devin-switch import personal.json --apply --name copy --force
devin-switch apply personal             # DRY-RUN as JSON (machine contract)
devin-switch apply personal --apply     # JSON result; same snapshot+verify path
devin-switch doctor                     # config sanity + closest profile
```

Every command accepts `--data-dir`, `--config-dir` and `--profiles-dir`
to override the platform defaults (`--config-dir` falls back to
`--data-dir` for single-root layouts). The profiles dir resolves in
order: `--profiles-dir` → `$DEVIN_SWITCH_PROFILES_DIR` → `./profiles` →
the bundled examples.

Exit code is `0` on success/clean dry-run and `1` on errors — and for
`doctor`, on any `FAIL` check (like `devin-doctor`).

## Profiles

A profile is a directory under `profiles/` whose files map 1:1 onto the
managed config space — see [`profiles/README.md`](profiles/README.md):

```
profiles/
  corporate/
    profile.json          # metadata only — never copied
    config.json           # → <data-dir>/config.json
    mcp_config.json       # → <data-dir>/mcp_config.json
    User/settings.json    # → <config-dir>/User/settings.json
  personal/
    ...
```

Overlays are **whole-file replacements**, not merges — what the profile
ships is what the file becomes. Files are JSONC: `//` and `/* */`
comments allowed, trailing commas not.

### Exporting and importing profiles

`export` serializes a profile to a single portable JSON document
(`format: "devin-switch/profile-archive"`, `schema_version: 1`): each
managed file as `{path, sha256, data}` with base64 bytes, plus the
`profile.json` metadata as fields. With no `--out` it goes to stdout;
`--out` writes a file but never overwrites an existing one.
`credentials.toml` is excluded without being read — archives carry
secrets in the clear, so keep them private.

`import` is dry-run by default like `use`: it validates the archive and
previews the profile that would be created. `--apply` materializes
`profiles/<name>/` (name from the archive or `--name`) via a staging dir
+ rename, and journals an `import` entry. Validation is **fail-closed**:
unknown `format`/`schema_version`, bad base64, sha256 mismatch,
duplicate paths, `..`/absolute/backslash/hidden/`__pycache__` path
parts, `profile.json` or `credentials.toml` entries are all rejected
before a single byte is written — archive paths can never escape the
profiles dir. An existing profile is never replaced without `--force`.

### The `lab` profile (G3 A/B runs)

`profiles/lab/` is a hermetic config for A/B skill evaluation: a pinned
model — `SWE-2-High` (free tier as of 2026-10-04), the same id in
`config.json`, `User/settings.json` and `profile.json`, **no hooks
and no MCP servers at all** — deliberate, since learning-loop /
prompt-logging hooks and the memory MCP would let attempt n learn from
attempt n-1 — plus `autoGenerateMemories` off. Label sessions `g3-ab`.
Caveat: it is still unconfirmed whether Devin can isolate the config dir
per workspace; if it can't, run attempts **serially** and let
`devin-switch` swap `lab` in/out between them (`rollback` restores the
previous bytes exactly). See `profiles/README.md`.

## What happens on `--apply`

1. **Plan** — per file: `create` / `modify` / `unchanged` / `skip`
   (`credentials.toml`, unsafe paths).
2. **Snapshot** — pre-switch bytes copied to
   `~/.config/Devin/.devin-ecosystem/switch-backups/<timestamp>/` with a
   `manifest.json` recording sha256 per file (`existed: false` for files
   the switch would create).
3. **Verify** — every copy must hash to its recorded sha256 *and* still
   match the live source (catches a mid-switch edit). Any problem →
   abort, nothing written, backup kept as evidence.
4. **Write** — atomic tmp+rename per file.
5. **Journal** — one JSON line appended to
   `.devin-ecosystem/switch-journal.jsonl`:
   `{ts, action, profile, files_changed, backup_dir}`.

`rollback` reads the newest `use` entry whose backup still exists,
restores the snapshot (and deletes files the switch created), then
appends a `rollback` entry — the backup is *not* consumed, so you can
switch forward again afterwards.

## Machine-readable apply (devin-devkit contract)

`devin-switch apply <profile>` runs the exact same
plan → snapshot → verify → write → journal path as `use`, with one
difference that matters to callers: **stdout is always a single JSON
document** — on dry-run, on a real apply and on failure — so a wrapper
never scrapes human output. The dry-run-by-default rule is unchanged;
`--apply` writes. This is the surface `devin-devkit` consumes when its
`apply` unifies tool install with config-profile selection.

```bash
devin-switch apply work            # JSON plan — writes nothing
devin-switch apply work --apply    # JSON result — files actually swapped
```

Success envelope (`schema: "devin-switch/apply@1"`):

```json
{
  "schema": "devin-switch/apply@1",
  "ok": true,
  "profile": "work",
  "dry_run": false,
  "actions": {"create": 1, "modify": 3},
  "files": [
    {"rel": "config.json", "target": "/abs/path", "action": "modify",
     "skip_reason": ""}
  ],
  "files_changed": ["config.json"],
  "skipped": [],
  "backup_dir": "/abs/.devin-ecosystem/switch-backups/<ts>",
  "journal": "/abs/.devin-ecosystem/switch-journal.jsonl",
  "rollback": "devin-switch rollback --apply"
}
```

Failures keep the shape: `ok: false`, an `error_type` —
`unknown_profile`, `invalid_manifest` (a `profile.json` that does not
parse fails closed here, where the read commands degrade to empty
metadata) or `snapshot_failed` — plus a human `error` string. The exit
code is still `1`, and a plain `error: …` line goes to stderr for logs;
stdout stays parseable either way.

**The handshake** (implemented devkit-side, in its repo): `devin-devkit
apply <profile>` resolves the profile in its own manifest — following
`alias_of` chains — installs the tool list, then shells out to
`devin-switch apply <resolved-name> --apply` to select the matching
config profile. The switch profile is looked up by that resolved name,
so a devkit profile `operations` applies `profiles/operations/`; the
name mapping and manifest resolution live in devin-devkit.

Same-env Python callers get the identical contract from
`devin_switch.apply_profile(name, roots, apply=True)`
(`devin_switch/api.py`, re-exported at the package root): it returns the
envelope as a dict and raises `ProfileError` → `unknown_profile`,
`ManifestError` → `invalid_manifest` and `SnapshotError` →
`snapshot_failed` for the failure modes above.

## Development

```bash
python -m pytest            # 128 tests, all on synthetic tmp-dir fixtures
python -m devin_switch.cli doctor --profiles-dir profiles
```

The test suite runs entirely against fake Devin roots — no real
installation is touched, and `credentials.toml` fixtures are only ever
checked by digest.

## Adapters (MCP server, Devin skill, plugin)

`devin_switch.mcp_server` exposes the read-only half of the CLI as MCP
tools (`switch_list_profiles`, `switch_preview`, `switch_diff`,
`switch_status`) via the `devin-switch-mcp` entry point
(`pip install 'devin-switch[mcp]'`). The write path stays CLI-only for
a human. Profile descriptions are passed through the same secret
masking the diff output uses before they reach the MCP client.

`adapters/` is a self-contained Devin plugin root
(`adapters/.devin-plugin/plugin.json` + `adapters/skills/devin-switch/SKILL.md`),
installed with
`devin plugins install Icaro0310/devin-control#packages/switch/adapters` —
it ships in the repo, not inside the wheel. The manifest launches the
server through `uvx --from 'devin-switch[mcp]' devin-switch-mcp`, which
resolves from PyPI as of 0.1.1 (the 0.1.0 wheel predates both the
entry point and the `mcp` extra). On 0.1.0 or a source checkout, run a
source-installed `devin-switch-mcp` directly or point a local manifest
copy at the checkout (`uvx --from './packages/switch[mcp]'
devin-switch-mcp`).

The same stdio server registers in other MCP hosts:

**Zed** — `context_servers` in `settings.json`:

```json
{
  "context_servers": {
    "devin-switch": { "command": { "path": "devin-switch-mcp", "args": [] } }
  }
}
```

**JetBrains** — Settings → Tools → AI Assistant → Model Context Protocol → Add:

```json
{ "mcpServers": { "devin-switch": { "command": "devin-switch-mcp" } } }
```

**Antigravity** — `~/.gemini/config/mcp_config.json`:

```json
{ "mcpServers": { "devin-switch": { "command": "devin-switch-mcp" } } }
```

## Related

- [`devin-doctor`](https://github.com/Icaro0310/devin-explore) — diagnoses
  a Devin installation (read-only, always); `devin-switch doctor` covers
  the config-file corner of that space, inline, with no dependency.
