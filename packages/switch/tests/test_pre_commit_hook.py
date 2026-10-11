"""Repo-root ``.pre-commit-hooks.yaml`` must stay invocable — the
declared entry maps to the real console script and a working
subcommand. No YAML dependency: the manifest is one hook entry whose
scalar fields are simple ``key: value`` lines."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
from devin_switch import cli

from tests.conftest import cli_argv

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / ".pre-commit-hooks.yaml"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

pytestmark = pytest.mark.skipif(
    not MANIFEST.exists(),
    reason=".pre-commit-hooks.yaml only exists in the monorepo checkout",
)


def _scalar_fields() -> dict[str, str]:
    text = MANIFEST.read_text(encoding="utf-8")
    # fields are `- key:` on the item line, then `  key:` inside it
    return dict(
        re.findall(r"^(?: {2}|- )(\w+):\s*(\S.*?)\s*$", text, re.MULTILINE)
    )


def test_manifest_declares_doctor_gate():
    fields = _scalar_fields()
    assert fields["id"] == "devin-switch-gate"
    assert fields["entry"] == "devin-switch doctor"
    # a system hook does not install its tool — the manifest must say
    # devin-switch has to be on PATH already
    assert fields["language"] == "system"
    assert "on PATH" in MANIFEST.read_text(encoding="utf-8")
    assert fields["pass_filenames"] == "false"
    assert fields["always_run"] == "true"


def test_hook_entry_is_invocable(roots):
    """The manifest argv must be the published console script + real
    subcommand args, exiting cleanly on a healthy config."""
    argv = shlex.split(_scalar_fields()["entry"])
    assert argv[0] == "devin-switch"
    assert 'devin-switch = "devin_switch.cli:main"' in PYPROJECT.read_text(
        encoding="utf-8"
    )
    assert cli.main(cli_argv(roots, *argv[1:])) == 0
