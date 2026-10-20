"""Publish-routing test for ``pypi-publish.yml``.

The ``resolve`` job maps a package selector (``office-v*`` tag or
``package=office`` dispatch input) to ``dir``/``mod``/``publisher`` /
``dist``/``cli`` outputs: the ``publish`` job picks its trusted-publisher
environment from that mapping, and the ``smoke`` job polls the ``dist``
project on PyPI and probes the ``cli`` console script. A wrong mapping
either fails the release, routes a wheel through the wrong approval
environment or smokes the wrong distribution, so the test executes the
workflow's own ``base`` expansion and ``case`` block through bash instead
of re-asserting a copy of it — a routing change in the workflow cannot
pass unnoticed.

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

# (dir, mod, publisher, dist, cli) — dist is the PyPI project name
# (orchestrator publishes as devin-fanout) and cli is the console script
# the smoke job probes, per each package's pyproject [project.scripts].
EXPECTED = {
    "orchestrator": ("packages/orchestrator", "devin_orchestrator", "pypi",
                     "devin-fanout", "devin-orchestrator"),
    "switch": ("packages/switch", "devin_switch", "pypi-switch",
               "devin-switch", "devin-switch"),
    "office": ("packages/office", "devin_office", "pypi-office",
               "devin-office", "devin-office"),
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
    """``<pkg>-vX.Y.Z`` tag resolves dir, module, publisher, dist and cli."""
    out = _route(f"{pkg}-v0.0.0")
    assert (out["dir"], out["mod"], out["publisher"],
            out["dist"], out["cli"]) == EXPECTED[pkg]


@pytest.mark.parametrize("pkg", sorted(EXPECTED))
def test_dispatch_route(pkg):
    """``package=<pkg>`` dispatch resolves the same mapping as its tag."""
    out = _route("refs/heads/main", sel=pkg)
    assert (out["dir"], out["mod"], out["publisher"],
            out["dist"], out["cli"]) == EXPECTED[pkg]


def test_dispatch_on_foreign_tag_keeps_selected_publisher():
    """Dispatch ``package=office`` on a ``switch-v*`` ref still routes to pypi-office.

    Regression for the review finding: the selector must win over the
    tag name so a wheel can never pair with the wrong trusted publisher.
    """
    out = _route("refs/tags/switch-v9.9.9", sel="office")
    assert out["dir"] == "packages/office"
    assert out["mod"] == "devin_office"
    assert out["publisher"] == "pypi-office"
    assert out["dist"] == "devin-office"
    assert out["cli"] == "devin-office"


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


# --- smoke job: PyPI index polling -----------------------------------------


def _poll_block() -> str:
    """Extract the ``run:`` body of the PyPI wheel-polling step verbatim."""
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(
        r"name: Wait for PyPI to serve the published wheel.*?run: \|(.*?)(?=\n      -|\Z)",
        text,
        re.DOTALL,
    )
    assert m is not None, "wheel-polling step not found in pypi-publish.yml"
    return "\n".join(line[8:] for line in m.group(1).splitlines())


def _run_poll(tmp_path: Path, index_html: str) -> subprocess.CompletedProcess[str]:
    """Run the workflow's real polling loop against a stubbed curl index."""
    wheel = "devin_office-0.1.0-py3-none-any.whl"
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / wheel).write_bytes(b"fake")
    (dist / "devin_office-0.1.0.tar.gz").write_bytes(b"fake")
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    (stub_dir / "curl").write_text(
        f"#!/usr/bin/env bash\nprintf '%s' {index_html!r}\n", encoding="utf-8"
    )
    (stub_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    # The workflow block calls `python` (provided by setup-python in CI);
    # delegate to the local interpreter when the bare name is absent.
    if shutil.which("python") is None:
        (stub_dir / "python").write_text(
            f"#!/usr/bin/env bash\nexec {sys.executable} \"$@\"\n", encoding="utf-8"
        )
    for stub in stub_dir.iterdir():
        os.chmod(stub, 0o755)
    fd, out_file = tempfile.mkstemp(prefix="ghout-")
    os.close(fd)
    env = dict(
        os.environ,
        DIST="devin-office",
        GITHUB_OUTPUT=out_file,
        PATH=f"{stub_dir}:{os.environ['PATH']}",
    )
    proc = subprocess.run(
        ["bash", "-c", _poll_block()],
        check=False, capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    os.unlink(out_file)
    return proc


def _index(*hrefs: str) -> str:
    links = "".join(f'<a href="https://files/{h}">{h}</a><br/>' for h in hrefs)
    return f"<!DOCTYPE html><html><body>{links}</body></html>"


def test_poll_passes_when_the_wheel_is_served(tmp_path: Path):
    """Wheel filename on the simple index → poll exits green."""
    proc = _run_poll(
        tmp_path,
        _index(
            "devin_office-0.1.0-py3-none-any.whl#sha256=abc",
            "devin_office-0.1.0.tar.gz",
        ),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_poll_fails_when_only_the_sdist_is_served(tmp_path: Path):
    """sdist alone must not satisfy the poll — the verified artifact is the
    wheel, and a version-only regex would pass on the sdist alone."""
    proc = _run_poll(tmp_path, _index("devin_office-0.1.0.tar.gz"))
    assert proc.returncode == 1
    assert "never appeared on PyPI" in proc.stdout


def test_poll_fails_on_a_different_version(tmp_path: Path):
    """An older wheel on the index is not this publish."""
    proc = _run_poll(
        tmp_path, _index("devin_office-0.0.9-py3-none-any.whl")
    )
    assert proc.returncode == 1
