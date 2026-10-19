# devin-control

<div align="center">

<a href="https://github.com/Icaro0310/devin-control/actions/workflows/ci.yml"><img src="https://github.com/Icaro0310/devin-control/actions/workflows/ci.yml/badge.svg" alt="ci"/></a>
<a href="https://scorecard.dev/viewer/?uri=github.com/Icaro0310/devin-control"><img src="https://api.scorecard.dev/projects/github.com/Icaro0310/devin-control/badge" alt="OpenSSF Scorecard"/></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"/></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+"/></a>
<a href="https://github.com/Icaro0310/devin-control"><img src="https://img.shields.io/github/stars/Icaro0310/devin-control" alt="GitHub stars"/></a>
<a href="https://github.com/Icaro0310/devin-control/commits/main"><img src="https://img.shields.io/github/last-commit/Icaro0310/devin-control" alt="Last commit"/></a>
<a href="https://github.com/Icaro0310/awesome-devin"><img src="https://img.shields.io/badge/part%20of-devin--*-ecosystem-7c3aed" alt="devin-* ecosystem"/></a>
<a href="https://github.com/Icaro0310/devin-control/issues"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs welcome"/></a>
</div>

<div align="center">

<a href="https://icaro0310.github.io/demos/devin-office.html"><img src="packages/office/src/devin_office/assets/demo.png" alt="devin-office demo: Devin sessions, subagents and tools as an animated circuit board (synthetic data)" width="80%"/></a>

<sub>**devin-office**: live Devin activity as an animated circuit board. The interactive demo (above) runs on synthetic data; a session kanban tracks RUNNING · BLOCKED · REVIEW · CLOSED · [open the kanban demo](https://icaro0310.github.io/demos/devin-office-kanban.html)</sub>

</div>

<!-- DEVIN-ECO:BEGIN -->
> **Part of the [DEVIN ecosystem](https://github.com/Icaro0310/awesome-devin)**  
> Track: Control · Nature: product  
> For: Security engineers, AI engineers, DevOps engineers  
> Interface: CLI / Bridge  
> Path: AI engineers · step 1/3 — before `devin-orchestrator`  
> Path: DevOps engineers · step 2/3 (as `devin-bridge`) — after `devin-state`, before `devin-switch`
<!-- DEVIN-ECO:END -->

<!-- DEVIN-WHERE:BEGIN -->
## Where this fits

- **Job:** Control
- **Product:** [`devin-control`](https://github.com/Icaro0310/devin-control)
- **Packages:** `bridge` · `office` · `orchestrator` · `switch`
- **Mode:** mixed
- **Ecosystem:** [`awesome-devin`](https://github.com/Icaro0310/awesome-devin) · registry: [`devin-powerups`](https://github.com/Icaro0310/devin-powerups)
<!-- DEVIN-WHERE:END -->

Control how Devin runs: a policy-gated ACP bridge, a background-worker
fan-out planner, config profile switching, and a live activity board —
the write/control side of the ecosystem.

| Package | Registry | What it does |
|---|---|---|
| [`packages/bridge`](packages/bridge) | [![@icaro0310/devin-bridge](https://img.shields.io/npm/v/@icaro0310%2fdevin-bridge)](https://www.npmjs.com/package/@icaro0310/devin-bridge) | Cross-platform policy-gated ACP bridge for Devin CLI — isolated sessions per project with fail-closed permission rules |
| [`packages/orchestrator`](packages/orchestrator) | [![devin-fanout](https://img.shields.io/pypi/v/devin-fanout)](https://pypi.org/project/devin-fanout/) | Background-worker fan-out policy: deterministic planner enforcing worker caps, no nesting, collect-before-report |
| [`packages/switch`](packages/switch) | `devin-switch` (PyPI, pending publish) | Switch between Devin configuration profiles (hooks, MCP, models) with snapshot and verification |
| [`packages/office`](packages/office) | `devin-office` (PyPI, pending publish) | Live Devin activity as an animated SVG circuit board: sessions, subagents, tools |

> **Renamed (Oct 2026):** this repository moved from `Icaro0310/devin-bridge` to `Icaro0310/devin-control` when it became the `devin-control` product workspace. The npm package `@icaro0310/devin-bridge`, the PyPI packages and all console scripts keep their names; stars, issues and history are preserved by the redirect.

## Layout

```
packages/<name>/   one installable package each — bridge is Node (npm),
                   orchestrator/switch/office are Python (uv workspace).
```

Each package ships independently: a tag `orchestrator-vX.Y.Z` or
`switch-vX.Y.Z` publishes only that PyPI package; a `bridge-vX.Y.Z` tag
creates a GitHub Release that publishes `@icaro0310/devin-bridge` to
npmjs and GitHub Packages. CI is scoped per path — a change under
`packages/switch/` runs only the switch suite.

The standalone `devin-orchestrator`, `devin-switch` and `devin-office`
repositories were absorbed into this workspace (F4.4); their histories
are preserved under `packages/` and the old repos are archived with
pointers here.

## Pre-commit gate

`.pre-commit-hooks.yaml` ships `devin-switch-gate`, a `language: system`
hook that runs `devin-switch doctor` — strictly read-only, exits 1 on
any FAIL — to block commits when the managed Devin config is broken:

```yaml
- repo: https://github.com/Icaro0310/devin-control
  rev: main   # or a tag cut after the hook lands — v0.1.0 predates it
  hooks: [{ id: devin-switch-gate }]
```

`language: system` means pre-commit does not install the tool —
`devin-switch` must be on PATH first (per-OS install steps live in the
[`packages/switch`](packages/switch) platform guides) or every commit
fails with "Executable `devin-switch` not found".

## Platform support

All packages support Linux and Windows (macOS support is planned, not
claimed; office's `/proc`-based panels and the flock session probe are
POSIX-only and degrade gracefully elsewhere). Per-package guides live
under `packages/<name>/`.

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.
