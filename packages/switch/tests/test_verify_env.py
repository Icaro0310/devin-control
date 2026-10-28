"""``devin-switch verify-env`` tests — deterministic environment
verification: files parse, required keys present, referenced paths
exist. Per-check results are explicit; exit code is 1 on any FAIL."""

from __future__ import annotations

import json

from devin_switch import cli
from devin_switch.health import verify_env

from tests.conftest import cli_argv


def test_verify_env_clean_roots_all_pass(roots):
    results = verify_env(roots)
    assert results  # files found
    assert all(r.status in ("PASS", "WARN") for r in results)
    labels = {r.label for r in results}
    assert "data/config.json" in labels
    assert "data/mcp_config.json" in labels
    assert "User/settings.json" in labels
    assert "credentials.toml" in labels


def test_verify_env_cli_exit_zero_on_clean(roots, capsys):
    rc = cli.main(cli_argv(roots, "verify-env"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS" in out
    assert "0 failed" in out


def test_verify_env_fails_on_malformed_file(roots, capsys):
    (roots.data_dir / "config.json").write_text(
        "{bad json — unclosed", encoding="utf-8"
    )
    rc = cli.main(cli_argv(roots, "verify-env"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL data/config.json" in out


def test_verify_env_trailing_comma_tolerated(roots, capsys):
    """JSONC with a trailing comma parses — it is tolerated, not an error."""
    (roots.data_dir / "config.json").write_text(
        '{"hooks": {},\n}', encoding="utf-8"
    )
    rc = cli.main(cli_argv(roots, "verify-env"))
    out = capsys.readouterr().out
    assert "FAIL data/config.json" not in out
    assert rc == 0


def test_verify_env_requires_mcpservers_key(roots, capsys):
    (roots.data_dir / "mcp_config.json").write_text(
        '{"unrelated": true}', encoding="utf-8"
    )
    rc = cli.main(cli_argv(roots, "verify-env"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing required key(s): mcpServers" in out


def test_verify_env_dead_command_path_fails(roots, capsys):
    (roots.data_dir / "mcp_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ghost": {"command": "/no/such/binary-xyz"}
                }
            }
        ),
        encoding="utf-8",
    )
    rc = cli.main(cli_argv(roots, "verify-env"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "command path does not exist" in out


def test_verify_env_bare_binary_command_passes(roots, capsys):
    """``command: "python"`` is a PATH lookup, not a path — no check."""
    (roots.data_dir / "mcp_config.json").write_text(
        json.dumps(
            {"mcpServers": {"ok": {"command": "python"}}}
        ),
        encoding="utf-8",
    )
    rc = cli.main(cli_argv(roots, "verify-env"))
    assert "FAIL data/mcp_config.json" not in capsys.readouterr().out
    assert rc == 0


def test_verify_env_dead_cwd_fails(roots, capsys, tmp_path):
    (roots.data_dir / "mcp_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "tool": {
                        "command": "python",
                        "cwd": str(tmp_path / "missing-dir"),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    rc = cli.main(cli_argv(roots, "verify-env"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "cwd is not a directory" in out


def test_verify_env_shape_errors_fail(roots, capsys):
    """A hooks file with a malformed group is an error, not a warning."""
    (roots.data_dir / "hooks.v1.json").write_text(
        json.dumps({"Stop": [{"hooks": [{"type": "bogus"}]}]}),
        encoding="utf-8",
    )
    rc = cli.main(cli_argv(roots, "verify-env"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL data/hooks.v1.json" in out


def test_verify_env_deterministic_repeat(roots):
    """Same roots, same results — no state carried between runs."""
    first = [(r.status, r.label, r.detail) for r in verify_env(roots)]
    second = [(r.status, r.label, r.detail) for r in verify_env(roots)]
    assert first == second


def test_verify_env_missing_credentials_warns(roots, monkeypatch, tmp_path):
    # keep the real machine's credentials.toml out of the fallback
    # candidates (_find_credentials also probes XDG/HOME locations)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-conf"))
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (roots.data_dir / "credentials.toml").unlink()
    results = verify_env(roots)
    cred = next(r for r in results if r.label == "credentials.toml")
    assert cred.status == "WARN"


def test_verify_env_empty_roots_warns(tmp_path, capsys):
    from devin_switch.paths import Roots

    bare = Roots(
        data_dir=tmp_path / "d",
        config_dir=tmp_path / "c",
        profiles_dir=tmp_path / "p",
    )
    bare.data_dir.mkdir()
    bare.config_dir.mkdir()
    rc = cli.main(cli_argv(bare, "verify-env"))
    out = capsys.readouterr().out
    assert rc == 0  # WARNs only — absent files are not FAILs
    assert "no managed config files" in out
