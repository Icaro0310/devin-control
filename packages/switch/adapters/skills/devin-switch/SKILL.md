---
name: devin-switch
description: "Check which Devin config profile is in effect, list available profiles, and preview what switching would change — when the user asks about their Devin config or is deciding between profiles. Read-only: consults and suggests, never writes."
triggers: [model, user]
allowed-tools:
  - exec
  - read
---

# devin-switch

When the user asks which Devin profile is active, whether their config is
healthy, or what switching profiles would change:

```bash
devin-switch doctor            # health check + closest profile
devin-switch list              # available profiles
devin-switch diff <a> <b>      # masked diff between two profiles
devin-switch use <profile>     # dry-run preview — writes nothing
```

Or, when this plugin's MCP server is connected, call `switch_status`,
`switch_list_profiles`, `switch_diff` or `switch_preview` — same data,
structured as JSON.

## Reading the result

- `switch_status` / `doctor`: `checks[]` carry `status` (PASS/WARN/FAIL),
  `label` and `detail`; `ok` is false when any check FAILs. `closest` and
  `profiles[]` rank each profile by distance — distance 0 means that
  profile exactly matches what is on disk right now.
- `switch_list_profiles` / `list`: name, managed-file count and
  description per profile under `profiles_dir`.
- `switch_diff` and `switch_preview`: `diff`/`plan` are masked text —
  values are redacted and credential-carrying files are withheld. In the
  preview, `files[]` carries each file's `action` (create, modify,
  unchanged, skip).
- `error` in the payload means it could not run (unknown profile name,
  bad path) — say so instead of inventing a verdict.

## Rules

- Read-only. Consult, compare and suggest — the apply step stays a
  human CLI action. If a change is wanted, the user runs
  `devin-switch use <profile>` at their own terminal; that command only
  previews, and the printed output says how to apply it.
- Never quote unmasked secrets — the masked output is all you get and
  all you should pass on.
- `credentials.toml` is never managed nor inspected — by design; do not
  flag its absence from a plan as a problem.
- Point `data_dir`/`config_dir`/`profiles_dir` (or `--data-dir` etc.) at
  non-default roots when the user's install is custom.
