"""MCP adapter contract: do_* mirrors the CLI's read surface on the
conftest fixtures; the tool layer never raises, and the module never
touches the write path (a source grep pins that)."""

from pathlib import Path

import pytest
import tomllib
from devin_switch import cli, mcp_actions, mcp_server
from devin_switch.health import check_config_files, rank_profiles
from devin_switch.plan import build_plan, profile_diff, render_plan
from devin_switch.profiles import discover, get

from tests.conftest import cli_argv, tree_snapshot

REPO_PROFILES = Path(__file__).resolve().parents[1] / "profiles"


def _kw(roots) -> dict:
    """The three root overrides, as MCP string params."""
    return {
        "data_dir": str(roots.data_dir),
        "config_dir": str(roots.config_dir),
        "profiles_dir": str(roots.profiles_dir),
    }


def _whole_tree(roots) -> dict[str, bytes]:
    snap = tree_snapshot(roots.data_dir)
    snap.update(
        {
            f"config:{rel}": data
            for rel, data in tree_snapshot(roots.config_dir).items()
        }
    )
    return snap


# ---------------------------------------------------------------------------
# switch_status — the `doctor` payload
# ---------------------------------------------------------------------------


def test_do_status_matches_doctor_output(roots, capsys):
    """do_status carries exactly what `devin-switch doctor` prints."""
    out = mcp_server.do_status(**_kw(roots))

    expected_checks = check_config_files(roots)
    assert out["checks"] == [
        {"status": r.status, "label": r.label, "detail": r.detail}
        for r in expected_checks
    ]
    assert out["ok"] is True
    assert out["closest"]["name"] == "home"  # closest to the fixture base
    assert out["closest"]["distance"] > 0

    ranked = rank_profiles(discover(roots.profiles_dir), roots)
    assert [(p["name"], p["distance"]) for p in out["profiles"]] == [
        (profile.name, d) for profile, d in ranked
    ]

    # text-equivalence: the CLI's lines render the same data
    assert cli.main(cli_argv(roots, "doctor")) == 0
    printed = capsys.readouterr().out
    for r in expected_checks:
        assert f"{r.status:<4} {r.label} — {r.detail}" in printed
    assert f"closest profile: {out['closest']['name']}" in printed


def test_do_status_flags_broken_config(roots):
    (roots.data_dir / "config.json").write_text("{ not json", "utf-8")
    out = mcp_server.do_status(**_kw(roots))
    assert out["ok"] is False
    assert any(c["status"] == "FAIL" for c in out["checks"])


# ---------------------------------------------------------------------------
# switch_list_profiles — the `list` payload
# ---------------------------------------------------------------------------


def test_do_list_profiles_matches_discover(roots):
    out = mcp_server.do_list_profiles(**_kw(roots))
    found = discover(roots.profiles_dir)
    assert out["profiles_dir"] == str(roots.profiles_dir)
    assert {p["name"] for p in out["profiles"]} == set(found)
    for entry in out["profiles"]:
        assert entry["files"] == len(found[entry["name"]].files)
        assert entry["description"] == found[entry["name"]].description


def test_do_list_profiles_masks_secret_in_description(roots, tmp_path):
    """SEC: a secret someone wrote into profile.json's free-text
    description reaches the MCP client masked, never raw."""
    from tests.conftest import write_profile
    write_profile(roots.profiles_dir, "leaky",
                  {"hooks.json": "{}"},
                  description="token=ghp_abc123secretvalue456")
    out = mcp_server.do_list_profiles(**_kw(roots))
    entry = next(p for p in out["profiles"] if p["name"] == "leaky")
    assert "ghp_abc123secretvalue456" not in entry["description"]
    assert "token=" in entry["description"]  # key kept, value masked


def test_do_list_profiles_empty_dir(roots, tmp_path):
    empty = tmp_path / "no-profiles"
    empty.mkdir()
    out = mcp_server.do_list_profiles(
        data_dir=str(roots.data_dir),
        config_dir=str(roots.config_dir),
        profiles_dir=str(empty),
    )
    assert out["profiles"] == []


def test_do_list_profiles_repo_profiles_include_lab(roots):
    """The real repo ``profiles/`` dir (lab, corporate, personal)."""
    out = mcp_server.do_list_profiles(
        data_dir=str(roots.data_dir),
        config_dir=str(roots.config_dir),
        profiles_dir=str(REPO_PROFILES),
    )
    assert {"lab", "corporate", "personal"} <= {
        p["name"] for p in out["profiles"]
    }


# ---------------------------------------------------------------------------
# switch_diff — the `diff` payload
# ---------------------------------------------------------------------------


def test_do_diff_matches_cli_text(roots, capsys):
    out = mcp_server.do_diff("work", "home", **_kw(roots))
    assert out["masked"] is True
    assert out["diff"] == profile_diff(
        get(roots.profiles_dir, "work"), get(roots.profiles_dir, "home")
    )

    assert cli.main(cli_argv(roots, "diff", "work", "home")) == 0
    printed = capsys.readouterr().out
    assert "diff work -> home" in printed
    assert out["diff"] in printed


def test_do_diff_unknown_profile_is_error_dict(roots):
    out = mcp_server.do_diff("work", "nope", **_kw(roots))
    assert out["error"] == "ProfileError"
    assert "nope" in out["detail"]


# ---------------------------------------------------------------------------
# switch_preview — the dry-run `use` plan
# ---------------------------------------------------------------------------


def test_do_preview_matches_dry_run_plan(roots, capsys):
    out = mcp_server.do_preview("work", **_kw(roots))
    plans = build_plan(get(roots.profiles_dir, "work"), roots)
    assert out["dry_run"] is True
    assert out["plan"] == render_plan(plans)
    assert out["files"] == [
        {
            "rel": fp.rel,
            "target": str(fp.target),
            "action": fp.action,
            "skip_reason": fp.skip_reason,
        }
        for fp in plans
    ]
    counts = {}
    for fp in plans:
        counts[fp.action] = counts.get(fp.action, 0) + 1
    assert out["actions"] == counts

    # text-equivalence: the CLI dry-run prints the same rendered plan
    assert cli.main(cli_argv(roots, "use", "work")) == 0
    printed = capsys.readouterr().out
    assert "DRY-RUN" in printed
    assert out["plan"] in printed


def test_do_preview_writes_nothing(roots):
    """The preview is a true dry-run: no config writes, no bookkeeping
    dirs created — the tree is byte-identical afterwards."""
    before = _whole_tree(roots)
    out = mcp_server.do_preview("work", **_kw(roots))
    assert "error" not in out
    assert _whole_tree(roots) == before
    assert not roots.ecosystem_dir.exists()


def test_do_preview_unknown_profile_is_error_dict(roots):
    out = mcp_server.do_preview("nope", **_kw(roots))
    assert out["error"] == "ProfileError"


# ---------------------------------------------------------------------------
# surface hygiene: the write path is never reachable from the adapter
# ---------------------------------------------------------------------------


def test_mcp_server_source_never_references_write_path():
    """The adapter must not import or name anything on the write path —
    only read functions from profiles/plan/health are allowed. That holds
    for the wiring (mcp_server) and the payload module (mcp_actions)."""
    for mod in (mcp_server, mcp_actions):
        src = (Path(mod.__file__)).read_text(encoding="utf-8")
        for banned in (
            "apply_plan",
            "apply_rollback",
            "plan_rollback",
            "atomic_write",
            "engine",
            "journal",
            "snapshot",
            "rollback",
            "--apply",
        ):
            assert banned not in src, (
                f"write-path reference in {mod.__name__}: {banned}")


def test_registered_tools_are_the_read_surface_only():
    """Even if mcp is absent, the tool names in the source are fixed."""
    src = (Path(mcp_server.__file__)).read_text(encoding="utf-8")
    for name in (
        "switch_status",
        "switch_list_profiles",
        "switch_diff",
        "switch_preview",
    ):
        assert f"def {name}(" in src


def test_build_server_registers_tools():
    pytest.importorskip("mcp")
    server = mcp_server.build_server()
    mgr = getattr(server, "_tool_manager", None) or getattr(
        server, "tools", None
    )
    assert mgr is not None
    tools = getattr(mgr, "_tools", mgr)
    assert isinstance(tools, dict)
    assert set(tools) == {
        "switch_status",
        "switch_list_profiles",
        "switch_diff",
        "switch_preview",
    }


def test_server_entrypoint_in_pyproject():
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    meta = tomllib.loads(pyproject)
    scripts = meta["project"]["scripts"]
    assert scripts["devin-switch-mcp"] == "devin_switch.mcp_server:main"
    assert any(
        dep.startswith("mcp")
        for dep in meta["project"]["optional-dependencies"]["mcp"]
    )


def _registered_tool_names() -> set[str]:
    """Tools the MCP server registers — derived statically so this test
    runs without the optional ``mcp`` extra installed."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).parents[1] / "src" / "devin_switch" / "mcp_server.py"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "tool"
            for dec in node.decorator_list
        )
    }


def test_mcp_tool_surface_is_pinned():
    """Regression contract: the AI surface is exactly this set. Static by
    design so it runs without the ``mcp`` extra; the runtime registry is
    pinned separately by test_build_server_registers_tools. A new tool
    only lands after a deliberate edit here — check it stays read-only
    before widening."""
    assert _registered_tool_names() == {"switch_diff","switch_list_profiles","switch_preview","switch_status"}
