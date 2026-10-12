# Changelog

## Unreleased

- **Docs** — corporate-Windows guides: Zscaler root export now covers both
  certificate stores (`certmgr.msc`/`certlm.msc`,
  `Cert:\CurrentUser\Root` + `Cert:\LocalMachine\Root`) and yields PEM
  (Base-64 wizard export, or `certutil -encode` after
  `Export-Certificate`, which writes DER).
- **Docs** — refreshed the generated `Part of the DEVIN ecosystem` block: journey recuration v2 (six paths, zero repeats, `Local-first ops` label, `devin-bridge` in DevOps).
- **Docs** — ecosystem journey recuration applied (six curated audiences); stale `Path:` line removed from the devin-bridge eco-block, which is not a registry entry.
- **CI** — Ruff lint job added (`astral-sh/ruff-action`, pinned);
  monitoring daemons keep documented fail-soft ignores by design.
- **Publish** — consolidated `pypi-publish.yml` builds and uploads
  `devin-fanout` and `devin-switch` via PyPI Trusted Publishing (OIDC),
  tag `*-v*` or manual dispatch. `devin-bridge` (npm/GPR) keeps its own
  workflows.

## 2026-10 (F4 consolidation)

- Packages consolidated into this repo:
  `devin-fanout` 0.2.0, `devin-switch` 0.1.0, `devin-bridge` (npm).
- Prior per-repo history lives in each package's git history.
