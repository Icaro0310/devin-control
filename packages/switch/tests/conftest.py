"""Fixtures — fully synthetic Devin roots and profiles.

No real Devin data is ever touched: every test runs against a tmp dir
that mimics the on-disk split (data dir holds config.json /
mcp_config.json / credentials.toml; config dir holds User/settings.json).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from devin_switch.paths import Roots

CREDENTIALS = (
    '[auth]\ntoken = "fixture-never-touched-0123456789abcdef0123456789"\n'
)

BASE_CONFIG = {
    "models": {
        "defaultModel": "SWE-16",
        "secondaryModel": "SWE-16-Fast",
        "heavyModel": "SWE-16",
        "disabledModels": [],
    },
    "cascade": {"autoExecution": "disabled", "enableCascade": True},
    "hooks": {},
}

BASE_MCP = {
    "mcpServers": {
        "local-tool": {
            "command": "python",
            "args": ["-m", "tool"],
            "env": {},
        }
    }
}

BASE_SETTINGS = {"window.zoomLevel": 1.0, "devin.autoContinue": 0}

WORK_FILES = {
    "config.json": json.dumps(
        {
            "models": {
                "defaultModel": "SWE-L15",
                "secondaryModel": "SWE-16-Fast",
                "heavyModel": "SWE-2-High",
                "disabledModels": [],
            },
            "cascade": {"autoExecution": "enabled", "enableCascade": True},
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo work-session",
                                "timeout": 10,
                            }
                        ],
                    }
                ]
            },
        },
        indent=2,
    ),
    "mcp_config.json": json.dumps(
        {
            "mcpServers": {
                "corp-gateway": {
                    "url": "https://mcp.corp.example.com/gw",
                    "env": {},
                }
            }
        },
        indent=2,
    ),
    # a file that does not exist yet in the fake install → exercises
    # 'create' on use and 'delete' on rollback
    "hooks.v1.json": json.dumps(
        {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {"type": "prompt", "prompt": "Be concrete."}
                    ]
                }
            ]
        },
        indent=2,
    ),
    "User/settings.json": json.dumps(
        {"window.zoomLevel": 1.5, "devin.autoContinue": 1}, indent=2
    ),
}

HOME_FILES = {
    "config.json": json.dumps(
        {
            "models": {
                "defaultModel": "SWE-16-Fast",
                "secondaryModel": "SWE-16",
                "heavyModel": "SWE-16",
                "disabledModels": ["SWE-2"],
            },
            "cascade": {"autoExecution": "disabled", "enableCascade": True},
        },
        indent=2,
    ),
    "User/settings.json": json.dumps({"window.zoomLevel": 1.0}, indent=2),
}


def write_profile(
    profiles_root: Path,
    name: str,
    files: dict[str, str],
    description: str = "",
    notes: list[str] | None = None,
) -> Path:
    pdir = profiles_root / name
    for rel, text in files.items():
        dest = pdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # bytes, not write_text: archive round-trips are byte-exact, so the
        # fixture must not let the platform translate \n -> \r\n on Windows
        dest.write_bytes(text.encode("utf-8"))
    meta: dict[str, object] = {}
    if description:
        meta["description"] = description
    if notes:
        meta["notes"] = notes
    if meta:
        (pdir / "profile.json").write_bytes(
            json.dumps(meta).encode("utf-8")
        )
    return pdir


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    """A fake Devin install: separate data/config dirs + two profiles."""
    data = tmp_path / "data"
    config = tmp_path / "config"
    data.mkdir()
    (config / "User").mkdir(parents=True)
    (data / "config.json").write_bytes(
        json.dumps(BASE_CONFIG, indent=2).encode("utf-8")
    )
    (data / "mcp_config.json").write_bytes(
        json.dumps(BASE_MCP, indent=2).encode("utf-8")
    )
    (data / "credentials.toml").write_bytes(CREDENTIALS.encode("utf-8"))
    (config / "User" / "settings.json").write_bytes(
        json.dumps(BASE_SETTINGS, indent=2).encode("utf-8")
    )

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    write_profile(profiles, "work", WORK_FILES, description="work overlay")
    write_profile(profiles, "home", HOME_FILES, description="home overlay")
    return Roots(
        data_dir=data, config_dir=config, profiles_dir=profiles
    )


def cli_argv(roots: Roots, *args: str) -> list[str]:
    """Subcommand args + the three root overrides the CLI needs."""
    return [
        *args,
        "--data-dir",
        str(roots.data_dir),
        "--config-dir",
        str(roots.config_dir),
        "--profiles-dir",
        str(roots.profiles_dir),
    ]


def tree_snapshot(root: Path) -> dict[str, bytes]:
    """rel-path → bytes for every file under root (non-existent → {})."""
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
