"""Version-sync guard: ``__version__`` must match ``[project].version``.

The publish workflow asserts the built wheel self-reports the pyproject
version (``pypi-publish.yml`` "Verify wheel self-reports pyproject
version"). This test catches the same drift in CI on every PR instead of
at release time. The pyproject parse mirrors the workflow's ``sed``
extraction so the two checks can never disagree on the source of truth.

Run with: ``PYTHONPATH=src python -m pytest tests/test_version.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import devin_orchestrator

PKG_DIR = Path(__file__).resolve().parents[1]


def test_version_matches_pyproject():
    """``devin_orchestrator.__version__`` equals the pyproject version."""
    pyproject = (PKG_DIR / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert m is not None, "no version line in pyproject.toml"
    assert devin_orchestrator.__version__ == m.group(1)
