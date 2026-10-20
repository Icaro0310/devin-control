"""Publish-routing test for ``pypi-publish.yml``.

The ``resolve`` job maps a package selector (``office-v*`` tag or
``package=office`` dispatch input) to ``dir``/``mod``/``publisher``
outputs, and the ``publish`` job picks its trusted-publisher
environment from that mapping. A wrong mapping either fails the release
or routes a wheel through the wrong approval environment, so the test
executes the workflow's own ``base`` expansion and ``case`` block
through bash instead of re-asserting a copy of it — a routing change in
the workflow cannot pass unnoticed.

Run with: ``python -m pytest packages/office/tests/test_publish_routing.py``
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pypi-publish.yml"

EXPECTED = {
    "orchestrator": ("packages/orchestrator", "devin_orchestrator", "pypi"),
    "switch": ("packages/switch", "devin_switch", "pypi-switch"),
    "office": ("packages/office", "devin_office", "pypi-office"),
}

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="routing test needs POSIX bash; on Windows `bash` may resolve to WSL",
)


def _case_block() -> str:
    """Extract the ``case ... esac`` routing block verbatim from the workflow."""
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"(case \"\$base\" in.*?esac)", text, re.DOTALL)
    assert m is not None, "routing case block not found in pypi-publish.yml"
    return m.group(1)


def _route(ref: str, sel: str = "") -> dict[str, str]:
    """Run the workflow's routing logic for REF/SEL and return its outputs."""
    fd, out_file = tempfile.mkstemp(prefix="ghout-")
    os.close(fd)
    env = dict(os.environ, REF=ref, SEL=sel, GITHUB_OUTPUT=out_file)
    # Same base expression as the workflow step, verbatim.
    script = f'base="${{SEL:-${{REF%%-v*}}}}"; {_case_block()}'
    proc = subprocess.run(["bash", "-c", script], check=False, capture_output=True, text=True, env=env)
    out = Path(out_file).read_text(encoding="utf-8")
    os.unlink(out_file)
    assert proc.returncode == 0, f"routing failed for ref={ref} sel={sel}: {proc.stderr}"
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


@pytest.mark.parametrize("pkg", sorted(EXPECTED))
def test_tag_route(pkg):
    """``<pkg>-vX.Y.Z`` tag resolves dir, module and publisher."""
    out = _route(f"{pkg}-v0.0.0")
    want_dir, want_mod, want_pub = EXPECTED[pkg]
    assert (out["dir"], out["mod"], out["publisher"]) == (want_dir, want_mod, want_pub)


@pytest.mark.parametrize("pkg", sorted(EXPECTED))
def test_dispatch_route(pkg):
    """``package=<pkg>`` dispatch resolves the same mapping as its tag."""
    out = _route("refs/heads/main", sel=pkg)
    want_dir, want_mod, want_pub = EXPECTED[pkg]
    assert (out["dir"], out["mod"], out["publisher"]) == (want_dir, want_mod, want_pub)


def test_dispatch_on_foreign_tag_keeps_selected_publisher():
    """Dispatch ``package=office`` on a ``switch-v*`` ref still routes to pypi-office.

    Regression for the review finding: the selector must win over the
    tag name so a wheel can never pair with the wrong trusted publisher.
    """
    out = _route("refs/tags/switch-v9.9.9", sel="office")
    assert out["dir"] == "packages/office"
    assert out["mod"] == "devin_office"
    assert out["publisher"] == "pypi-office"


def test_unknown_package_fails_closed():
    """An unmapped selector exits non-zero instead of guessing."""
    fd, out_file = tempfile.mkstemp(prefix="ghout-")
    os.close(fd)
    env = dict(os.environ, REF="refs/tags/mystery-v1.0.0", SEL="", GITHUB_OUTPUT=out_file)
    script = f'base="${{SEL:-${{REF%%-v*}}}}"; {_case_block()}'
    proc = subprocess.run(["bash", "-c", script], check=False, capture_output=True, text=True, env=env)
    os.unlink(out_file)
    assert proc.returncode != 0
    assert "unknown package" in proc.stderr
