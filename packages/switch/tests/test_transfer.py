"""Portable profile archives: export / import round-trip, schema and
path-safety validation, name collisions, credential hygiene."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from devin_switch import cli, journal
from devin_switch.transfer import parse_archive

from tests.conftest import WORK_FILES, cli_argv, tree_snapshot, write_profile


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _archive(
    files: dict[str, bytes],
    name: str = "imported",
    **overrides,
) -> dict:
    payload = {
        "format": "devin-switch/profile-archive",
        "schema_version": 1,
        "name": name,
        "files": [
            {
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": _b64(data),
            }
            for rel, data in sorted(files.items())
        ],
    }
    payload.update(overrides)
    return payload


def _write_archive(tmp_path: Path, obj: dict | str) -> Path:
    dest = tmp_path / "archive.json"
    text = obj if isinstance(obj, str) else json.dumps(obj)
    dest.write_text(text, encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_stdout_emits_valid_archive(roots, capsys):
    assert cli.main(cli_argv(roots, "export", "work")) == 0
    payload = capsys.readouterr().out
    archive = parse_archive(payload)  # validates, raises on bad schema
    assert archive.name == "work"
    assert archive.description == "work overlay"
    got = {f.rel: f.data for f in archive.files}
    for rel, text in WORK_FILES.items():
        assert got[rel] == text.encode("utf-8"), rel
    # profile.json is metadata — exported as fields, never as a file
    assert "profile.json" not in got


def test_export_out_writes_file(roots, tmp_path, capsys):
    dest = tmp_path / "work.devin-profile.json"
    assert (
        cli.main(cli_argv(roots, "export", "work", "--out", str(dest)))
        == 0
    )
    out = capsys.readouterr().out
    assert "exported profile 'work'" in out
    archive = json.loads(dest.read_text(encoding="utf-8"))
    assert archive["format"] == "devin-switch/profile-archive"
    assert archive["schema_version"] == 1
    assert archive["name"] == "work"


def test_export_refuses_to_overwrite_out(roots, tmp_path, capsys):
    dest = tmp_path / "work.json"
    dest.write_text("keep me", encoding="utf-8")
    assert (
        cli.main(cli_argv(roots, "export", "work", "--out", str(dest)))
        == 1
    )
    assert "refusing to overwrite" in capsys.readouterr().err
    assert dest.read_text(encoding="utf-8") == "keep me"


def test_export_unknown_profile(roots, capsys):
    assert cli.main(cli_argv(roots, "export", "nope")) == 1
    assert "unknown profile" in capsys.readouterr().err


def test_export_skips_credentials_and_warns(roots, capsys):
    write_profile(
        roots.profiles_dir,
        "leaky",
        {
            "credentials.toml": '[auth]\ntoken = "never"\n',
            "secrets.json": '{"api_key": "abc"}',
            "config.json": "{}",
        },
    )
    assert cli.main(cli_argv(roots, "export", "leaky")) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    paths = {f["path"] for f in payload["files"]}
    # never-touch: not even read into the archive
    assert "credentials.toml" not in paths
    # credential-named: exported (the user asked) but flagged on stderr
    assert "secrets.json" in paths
    assert "never managed" in captured.err
    assert "credential-named" in captured.err


# ---------------------------------------------------------------------------
# round-trip
# ---------------------------------------------------------------------------


def test_export_import_round_trip(roots, tmp_path, capsys):
    dest = tmp_path / "work.json"
    assert (
        cli.main(cli_argv(roots, "export", "work", "--out", str(dest)))
        == 0
    )
    capsys.readouterr()
    assert (
        cli.main(
            cli_argv(
                roots, "import", str(dest), "--name", "work-copy", "--apply"
            )
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "imported profile 'work-copy'" in out

    new_root = roots.profiles_dir / "work-copy"
    old_root = roots.profiles_dir / "work"
    new_snap, old_snap = tree_snapshot(new_root), tree_snapshot(old_root)
    # managed files round-trip byte-for-byte; profile.json is regenerated
    # (formatting may differ) so it is compared semantically
    new_meta = json.loads(new_snap.pop("profile.json"))
    old_meta = json.loads(old_snap.pop("profile.json"))
    assert new_snap == old_snap
    assert new_meta == old_meta

    # the imported profile is a first-class citizen
    assert cli.main(cli_argv(roots, "use", "work-copy", "--apply")) == 0
    assert (roots.data_dir / "hooks.v1.json").is_file()

    entries = journal.read_all(roots.journal_path)
    assert entries[0]["action"] == "import"
    assert entries[0]["profile"] == "work-copy"
    assert entries[1]["action"] == "use"


# ---------------------------------------------------------------------------
# import — dry-run / apply / collisions
# ---------------------------------------------------------------------------


def test_import_dry_run_writes_nothing(roots, tmp_path, capsys):
    archive = _archive({"config.json": b"{}"})
    src = _write_archive(tmp_path, archive)
    before = tree_snapshot(roots.profiles_dir)
    assert cli.main(cli_argv(roots, "import", str(src))) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "imported" in out or "would create" in out
    assert "--apply" in out
    assert tree_snapshot(roots.profiles_dir) == before
    assert not roots.ecosystem_dir.exists()


def test_import_apply_creates_profile(roots, tmp_path, capsys):
    files = {
        "config.json": b'{"models": {}}',
        "User/settings.json": b'{"window.zoomLevel": 2}',
    }
    src = _write_archive(tmp_path, _archive(files, description="from afar"))
    assert cli.main(cli_argv(roots, "import", str(src), "--apply")) == 0
    out = capsys.readouterr().out
    assert "imported profile 'imported'" in out
    root = roots.profiles_dir / "imported"
    assert (root / "config.json").read_bytes() == files["config.json"]
    assert (
        root / "User" / "settings.json"
    ).read_bytes() == files["User/settings.json"]
    meta = json.loads((root / "profile.json").read_text("utf-8"))
    assert meta["description"] == "from afar"


def test_import_name_collision_refused_without_force(
    roots, tmp_path, capsys
):
    src = _write_archive(
        tmp_path, _archive({"config.json": b"{}"}, name="work")
    )
    before = tree_snapshot(roots.profiles_dir / "work")
    assert cli.main(cli_argv(roots, "import", str(src), "--apply")) == 1
    assert "already exists" in capsys.readouterr().err
    assert tree_snapshot(roots.profiles_dir / "work") == before


def test_import_name_override_still_collides(roots, tmp_path, capsys):
    src = _write_archive(
        tmp_path, _archive({"config.json": b"{}"}, name="fresh")
    )
    assert (
        cli.main(
            cli_argv(roots, "import", str(src), "--name", "home", "--apply")
        )
        == 1
    )
    assert "already exists" in capsys.readouterr().err


def test_import_force_replaces_profile(roots, tmp_path):
    files = {"mcp_config.json": b'{"mcpServers": {}}'}
    src = _write_archive(tmp_path, _archive(files, name="work"))
    assert (
        cli.main(
            cli_argv(roots, "import", str(src), "--apply", "--force")
        )
        == 0
    )
    snap = tree_snapshot(roots.profiles_dir / "work")
    assert snap == {"mcp_config.json": files["mcp_config.json"]}


def test_import_name_override_creates_under_new_name(roots, tmp_path):
    src = _write_archive(
        tmp_path, _archive({"config.json": b"{}"}, name="ignored")
    )
    assert (
        cli.main(
            cli_argv(
                roots, "import", str(src), "--name", "renamed", "--apply"
            )
        )
        == 0
    )
    assert (roots.profiles_dir / "renamed" / "config.json").is_file()
    assert not (roots.profiles_dir / "ignored").exists()


# ---------------------------------------------------------------------------
# import — fail-closed validation
# ---------------------------------------------------------------------------


def test_import_rejects_unrecognized_format(roots, tmp_path, capsys):
    for bad in [
        _archive({"a.json": b"{}"}, format="something/else"),
        _archive({"a.json": b"{}"}, format=None),
        _archive({"a.json": b"{}"}, schema_version=2),
        _archive({"a.json": b"{}"}, schema_version="1"),
        "not json at all {",
        '["a", "list"]',
    ]:
        src = _write_archive(tmp_path, bad)
        assert cli.main(cli_argv(roots, "import", str(src), "--apply")) == 1
        assert not (roots.profiles_dir / "imported").exists()


def test_import_rejects_sha256_mismatch(roots, tmp_path, capsys):
    archive = _archive({"config.json": b"{}"})
    archive["files"][0]["data"] = _b64(b'{"tampered": true}')
    src = _write_archive(tmp_path, archive)
    assert cli.main(cli_argv(roots, "import", str(src), "--apply")) == 1
    assert "sha256 mismatch" in capsys.readouterr().err
    assert not (roots.profiles_dir / "imported").exists()


def test_import_rejects_path_traversal(roots, tmp_path):
    evil_paths = [
        "../escaped.json",
        "User/../../outside.json",
        "/abs/config.json",
        "a\\b.json",
        "sub/../../x.json",
        "./config.json",
        "a//b.json",
        ".hidden.json",
        "__pycache__/x.json",
        "profile.json",
        "credentials.toml",
        "sub/credentials.toml",
    ]
    sentinel = roots.profiles_dir.parent / "escaped.json"
    for rel in evil_paths:
        archive = _archive({rel: b"evil"}, name=f"evil-{len(rel)}")
        src = _write_archive(tmp_path, archive)
        assert (
            cli.main(cli_argv(roots, "import", str(src), "--apply")) == 1
        ), rel
        # nothing materialized anywhere — no traversal, no partial profile
        assert not sentinel.exists()
        assert not (roots.profiles_dir / f"evil-{len(rel)}").exists()


def test_import_rejects_bad_names(roots, tmp_path, capsys):
    for bad_name in ["../x", "a/b", ".hidden", "", "has space", "a\\b"]:
        src = _write_archive(
            tmp_path, _archive({"config.json": b"{}"}, name=bad_name)
        )
        assert (
            cli.main(cli_argv(roots, "import", str(src), "--apply")) == 1
        ), bad_name
    assert "invalid profile name" in capsys.readouterr().err


def test_import_rejects_duplicate_paths(roots, tmp_path):
    archive = _archive({"config.json": b"{}"})
    archive["files"].append(dict(archive["files"][0]))
    src = _write_archive(tmp_path, archive)
    assert cli.main(cli_argv(roots, "import", str(src), "--apply")) == 1


def test_import_missing_file(roots, tmp_path, capsys):
    missing = tmp_path / "nope.json"
    assert cli.main(cli_argv(roots, "import", str(missing), "--apply")) == 1
    assert "cannot read" in capsys.readouterr().err


def test_import_never_leaves_staging_dir(roots, tmp_path):
    src = _write_archive(tmp_path, _archive({"config.json": b"{}"}))
    cli.main(cli_argv(roots, "import", str(src), "--apply"))
    leftovers = [
        p for p in roots.profiles_dir.iterdir() if p.name.startswith(".")
    ]
    assert leftovers == []
