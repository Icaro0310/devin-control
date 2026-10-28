"""``devin-switch apply`` — the JSON contract devin-devkit consumes.

The whole point of the subcommand is that stdout is *always* a single
``devin-switch/apply@1`` JSON document — on dry-run, on a real apply and
on failure — so a subprocess caller parses stdout and checks ``ok`` /
``error_type`` + the exit code, never stderr.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from devin_switch import api, cli, journal
from devin_switch.profiles import ProfileError

from tests.conftest import WORK_FILES, cli_argv, tree_snapshot, write_profile


def _run(roots, capsys, *args: str) -> tuple[int, dict, str]:
    code = cli.main(cli_argv(roots, "apply", *args))
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


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
# dry-run: same safety invariant as `use`
# ---------------------------------------------------------------------------


def test_apply_dry_run_writes_nothing(roots, capsys):
    before = _whole_tree(roots)
    code, payload, err = _run(roots, capsys, "work")
    assert code == 0
    assert err == ""
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["files_changed"] == []
    assert payload["backup_dir"] is None
    # nothing changed, nothing created — not even .devin-ecosystem/
    assert _whole_tree(roots) == before
    assert not roots.ecosystem_dir.exists()


# ---------------------------------------------------------------------------
# apply: the success path devkit wraps
# ---------------------------------------------------------------------------


def test_apply_writes_and_reports(roots, capsys):
    original_creds = (roots.data_dir / "credentials.toml").read_bytes()
    code, payload, _ = _run(roots, capsys, "work", "--apply")
    assert code == 0
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["profile"] == "work"
    assert sorted(payload["files_changed"]) == sorted(WORK_FILES)
    # plan carries per-file action + absolute target
    by_rel = {f["rel"]: f for f in payload["files"]}
    assert by_rel["config.json"]["action"] == "modify"
    assert by_rel["hooks.v1.json"]["action"] == "create"
    assert by_rel["config.json"]["target"] == str(
        roots.data_dir / "config.json"
    )
    # snapshot was taken and journal holds the use entry
    backup = payload["backup_dir"]
    assert backup and (Path(backup) / "manifest.json").is_file()
    entry = journal.last_use_entry(roots.journal_path)
    assert entry is not None and entry["profile"] == "work"
    assert entry["backup_dir"] == backup
    # the write really happened
    assert (roots.data_dir / "config.json").read_bytes() == (
        roots.profiles_dir / "work" / "config.json"
    ).read_bytes()
    assert (roots.data_dir / "credentials.toml").read_bytes() == (
        original_creds
    )


def test_apply_already_in_effect_reports_noop(roots, capsys):
    assert cli.main(cli_argv(roots, "apply", "work", "--apply")) == 0
    capsys.readouterr()
    code, payload, _ = _run(roots, capsys, "work", "--apply")
    assert code == 0
    assert payload["ok"] is True
    assert payload["files_changed"] == []
    assert payload["actions"].get("unchanged", 0) == len(WORK_FILES)


# ---------------------------------------------------------------------------
# failure modes — stdout still parses as JSON, exit code 1
# ---------------------------------------------------------------------------


def test_apply_unknown_profile(roots, capsys):
    before = _whole_tree(roots)
    code, payload, err = _run(roots, capsys, "nope", "--apply")
    assert code == 1
    assert payload["ok"] is False
    assert payload["error_type"] == "unknown_profile"
    assert "nope" in payload["error"]
    assert "work" in payload["error"]  # available profiles listed
    assert "error:" in err
    assert _whole_tree(roots) == before


def test_apply_malformed_manifest(roots, capsys):
    pdir = write_profile(roots.profiles_dir, "broken", {"config.json": "{}"})
    (pdir / "profile.json").write_bytes(b"{ not json")
    before = _whole_tree(roots)
    code, payload, err = _run(roots, capsys, "broken", "--apply")
    assert code == 1
    assert payload["ok"] is False
    assert payload["error_type"] == "invalid_manifest"
    assert "profile.json" in payload["error"]
    assert "error:" in err
    assert _whole_tree(roots) == before
    # the malformed profile also fails the dry-run half
    code, payload, _ = _run(roots, capsys, "broken")
    assert code == 1
    assert payload["error_type"] == "invalid_manifest"


# ---------------------------------------------------------------------------
# safety invariants hold under the machine verb too
# ---------------------------------------------------------------------------


def test_apply_skips_credentials_toml(roots, capsys):
    write_profile(
        roots.profiles_dir,
        "cred",
        {"config.json": "{}", "credentials.toml": '[auth]\ntoken = "x"\n'},
    )
    original = (roots.data_dir / "credentials.toml").read_bytes()
    code, payload, _ = _run(roots, capsys, "cred", "--apply")
    assert code == 0
    assert "credentials.toml" in payload["skipped"]
    assert "credentials.toml" not in payload["files_changed"]
    assert (roots.data_dir / "credentials.toml").read_bytes() == original


def test_apply_envelope_shape_is_the_contract(roots, capsys):
    """The exact key set a devkit caller may rely on (apply@1)."""
    code, payload, _ = _run(roots, capsys, "work")
    assert code == 0
    assert payload["schema"] == "devin-switch/apply@1"
    assert set(payload) == {
        "schema",
        "ok",
        "profile",
        "dry_run",
        "actions",
        "files",
        "files_changed",
        "skipped",
        "backup_dir",
        "journal",
        "rollback",
    }
    assert payload["journal"] == str(roots.journal_path)
    assert payload["rollback"] == "devin-switch rollback --apply"
    for entry in payload["files"]:
        assert set(entry) == {"rel", "target", "action", "skip_reason"}
        assert entry["action"] in {"create", "modify", "unchanged", "skip"}


# ---------------------------------------------------------------------------
# the same contract as a Python callable
# ---------------------------------------------------------------------------


def test_apply_profile_callable(roots):
    payload = api.apply_profile("work", roots)
    assert payload["schema"] == api.SCHEMA
    assert payload["dry_run"] is True
    assert not roots.ecosystem_dir.exists()
    payload = api.apply_profile("work", roots, apply=True)
    assert payload["files_changed"]
    assert Path(payload["backup_dir"]).is_dir()


def test_apply_profile_callable_errors(roots):
    with pytest.raises(ProfileError):
        api.apply_profile("nope", roots)
    pdir = write_profile(roots.profiles_dir, "bad", {"config.json": "{}"})
    (pdir / "profile.json").write_bytes(b"[1, 2]")
    with pytest.raises(api.ManifestError):
        api.apply_profile("bad", roots)


def test_apply_profile_exported_at_package_root(roots):
    import devin_switch

    assert devin_switch.apply_profile is api.apply_profile


def test_resolve_roots_parity(roots):
    resolved = api.resolve_roots(
        roots.data_dir, roots.config_dir, roots.profiles_dir
    )
    assert resolved == roots
