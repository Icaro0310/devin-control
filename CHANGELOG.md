# Changelog

## Unreleased

- **CI** — post-publish install smoke on both distribution channels:
  `publish-bridge-npm.yml` polls npm for the published bridge version and
  runs `devin-bridge --help` from a global install; `pypi-publish.yml`
  gains a neutral-env `smoke` job that waits for the uploaded wheel's
  filename on the PyPI simple index and runs the resolved console script
  from a throwaway venv. Smoke runs only on the real publish path, never
  on the duplicate-skip leg.
- **Docs** — root README embeds the devin-office demo image under the
  top badge block, linking to the hosted interactive demo and the
  kanban demo.
- **Docs** — corporate-Windows guides: Zscaler root export now covers both
  certificate stores (`certmgr.msc`/`certlm.msc`,
  `Cert:\CurrentUser\Root` + `Cert:\LocalMachine\Root`) and yields PEM
  (Base-64 wizard export, or `certutil -encode` after
  `Export-Certificate`, which writes DER).
- **Hooks** — `.pre-commit-hooks.yaml` adds `devin-switch-gate`, a
  `language: system` hook running `devin-switch doctor` (read-only;
  exits 1 on any FAIL) so consumers can gate commits on Devin config
  sanity. Requires `devin-switch` on PATH.
- **Docs** — refreshed the generated `Part of the DEVIN ecosystem` block: journey recuration v2 (six paths, zero repeats, `Local-first ops` label, `devin-bridge` in DevOps).
- **Docs** — ecosystem journey recuration applied (six curated audiences); stale `Path:` line removed from the devin-bridge eco-block, which is not a registry entry.
- **CI** — Ruff lint job added (`astral-sh/ruff-action`, pinned);
  monitoring daemons keep documented fail-soft ignores by design.
- **Publish** — consolidated `pypi-publish.yml` builds and uploads
  `devin-fanout`, `devin-switch` and `devin-office` via PyPI Trusted
  Publishing (OIDC), tag `*-v*` or manual dispatch; the publish
  environment follows the resolved package. `devin-bridge` (npm/GPR)
  keeps its own workflows.

## 2026-10 (F4 consolidation)

- Packages consolidated into this repo:
  `devin-fanout` 0.2.0, `devin-switch` 0.1.0, `devin-bridge` (npm).
- Prior per-repo history lives in each package's git history.
