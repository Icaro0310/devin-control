"""Profile discovery, path mapping, bundled mirror and JSONC parsing."""

from __future__ import annotations

import filecmp
import json
from pathlib import Path

import pytest
from devin_switch import paths
from devin_switch.jsonc import load_jsonc, strip_jsonc
from devin_switch.plan import build_plan
from devin_switch.profiles import discover, get, load_profile

from tests.conftest import write_profile

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_discover_finds_profiles(roots):
    found = discover(roots.profiles_dir)
    assert set(found) == {"work", "home"}
    assert found["work"].description == "work overlay"
    assert "User/settings.json" in found["work"].files
    # profile.json is metadata, never a managed file
    assert "profile.json" not in found["work"].files


def test_get_unknown_raises(roots):
    with pytest.raises(Exception, match="unknown profile"):
        get(roots.profiles_dir, "ghost")


def test_dotfiles_and_meta_not_managed(roots):
    write_profile(
        roots.profiles_dir,
        "messy",
        {"config.json": "{}", ".hidden": "x"},
    )
    (roots.profiles_dir / "messy" / ".gitkeep").write_text("")
    p = load_profile(roots.profiles_dir / "messy")
    assert set(p.files) == {"config.json"}


def test_target_for_routing(roots):
    assert (
        paths.target_for("User/settings.json", roots)
        == roots.config_dir / "User" / "settings.json"
    )
    assert (
        paths.target_for("config.json", roots)
        == roots.data_dir / "config.json"
    )
    for bad in ("../evil", "User/../../evil", "/abs/path"):
        with pytest.raises(ValueError):
            paths.target_for(bad, roots)


def test_build_plan_actions(roots):
    profile = get(roots.profiles_dir, "work")
    plans = {fp.rel: fp.action for fp in build_plan(profile, roots)}
    assert plans["config.json"] == "modify"
    assert plans["mcp_config.json"] == "modify"
    assert plans["User/settings.json"] == "modify"
    assert plans["hooks.v1.json"] == "create"


def test_credentials_in_profile_planned_as_skip(roots):
    write_profile(
        roots.profiles_dir, "evil", {"credentials.toml": "x = 1\n"}
    )
    profile = get(roots.profiles_dir, "evil")
    plans = {fp.rel: fp for fp in build_plan(profile, roots)}
    assert plans["credentials.toml"].action == "skip"
    assert "never managed" in plans["credentials.toml"].skip_reason


def test_strip_jsonc():
    text = '{\n// c\n"a": 1, /* c */ "b": "http://x" // tail\n}'
    assert json.loads(strip_jsonc(text)) == {"a": 1, "b": "http://x"}


def test_load_jsonc_real_files(roots):
    obj = load_jsonc(roots.data_dir / "config.json")
    assert obj["models"]["defaultModel"] == "SWE-16"


def test_bundled_profiles_mirror_repo_examples():
    """The packaged profiles under src/devin_switch/profiles/ must be an
    exact copy of the repo-root profiles/ examples."""
    repo = REPO_ROOT / "profiles"
    bundled = paths.bundled_profiles_dir()
    assert repo.is_dir() and bundled.is_dir()
    comparison = filecmp.dircmp(repo, bundled)

    def assert_same(dcmp):
        assert dcmp.left_only == [] and dcmp.right_only == []
        assert dcmp.diff_files == [] and dcmp.funny_files == []
        for sub in dcmp.subdirs.values():
            assert_same(sub)

    assert_same(comparison)


def test_bundled_profiles_parse_as_jsonc():
    """Every shipped overlay must parse — comments and trailing commas
    tolerated, everything else raises."""
    for profile in discover(paths.bundled_profiles_dir()).values():
        for rel, src in profile.files.items():
            if rel.endswith(".json"):
                load_jsonc(src)  # raises on malformed JSONC
