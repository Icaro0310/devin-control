"""devin-switch as an MCP server: the read-only surface exposed as tools.

Four tools — ``switch_status``, ``switch_list_profiles``, ``switch_diff``
and ``switch_preview`` — return the same data the CLI's read subcommands
print (``doctor``, ``list``, ``diff``, and the dry-run half of ``use``).
``switch_preview`` is the dry-run plan only: it reads the profile and the
live targets, computes actions, and writes nothing — exactly like the
CLI's dry-run, which never creates bookkeeping dirs either.

Read-only by construction: only ``profiles``, ``plan`` and ``health``
are imported (via ``mcp_actions``). The write path stays exclusively in
the CLI where a human confirms it — nothing here can modify the user's
config.

The logic lives in ``devin_switch.mcp_actions`` ``do_*`` functions,
unit-testable without a running server or the ``mcp`` package. This
module is only wiring — needs the ``mcp`` extra:
``pip install 'devin-switch[mcp]'``.
"""

from __future__ import annotations

from devin_switch.mcp_actions import (
    _err,
    do_diff,
    do_list_profiles,
    do_preview,
    do_status,
)

__all__ = [
    "build_server",
    "do_diff",
    "do_list_profiles",
    "do_preview",
    "do_status",
    "main",
]


def _make_app(name: str):
    """Return an MCP server app across SDK versions.

    mcp 2.x renamed FastMCP -> MCPServer; both expose the same .tool()
    decorator and .run(transport='stdio'). Support whichever is installed.
    """
    try:  # mcp 2.x
        from mcp.server.mcpserver import MCPServer
        return MCPServer(name)
    except ImportError:
        pass
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP
        return FastMCP(name)
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "The MCP server needs the 'mcp' extra: "
            "pip install 'devin-switch[mcp]'"
        ) from e


def build_server():
    """Build the MCP server app with every devin-switch tool registered."""
    server = _make_app("devin-switch")

    @server.tool()
    def switch_status(
        data_dir: str = "", config_dir: str = "", profiles_dir: str = ""
    ) -> dict:
        """Health-check the Devin config like ``devin-switch doctor``:
        per-file PASS/WARN/FAIL checks plus profiles ranked by distance
        (distance 0 = the profile currently in effect). Read-only.
        ``ok`` is false when any check FAILs; ``error`` means it could
        not run at all.
        """
        try:
            return do_status(
                data_dir=data_dir,
                config_dir=config_dir,
                profiles_dir=profiles_dir,
            )
        except Exception as error:  # noqa: BLE001 — tool boundary must not raise
            return _err(error)

    @server.tool()
    def switch_list_profiles(
        data_dir: str = "", config_dir: str = "", profiles_dir: str = ""
    ) -> dict:
        """List the profiles ``devin-switch list`` would show: name,
        managed-file count and description for each profile discovered
        under ``profiles_dir`` (or the default location). Read-only.
        """
        try:
            return do_list_profiles(
                data_dir=data_dir,
                config_dir=config_dir,
                profiles_dir=profiles_dir,
            )
        except Exception as error:  # noqa: BLE001 — tool boundary must not raise
            return _err(error)

    @server.tool()
    def switch_diff(
        a: str,
        b: str,
        data_dir: str = "",
        config_dir: str = "",
        profiles_dir: str = "",
    ) -> dict:
        """Masked per-file diff between two profiles, like
        ``devin-switch diff <a> <b>`` — values are redacted and
        credential-carrying files are withheld. Read-only. ``error`` in
        the payload means an unknown profile name.
        """
        try:
            return do_diff(
                a=a,
                b=b,
                data_dir=data_dir,
                config_dir=config_dir,
                profiles_dir=profiles_dir,
            )
        except Exception as error:  # noqa: BLE001 — tool boundary must not raise
            return _err(error)

    @server.tool()
    def switch_preview(
        profile: str,
        data_dir: str = "",
        config_dir: str = "",
        profiles_dir: str = "",
    ) -> dict:
        """Preview what switching to ``profile`` would change — the same
        plan the CLI's dry-run prints: per-file action
        (create/modify/unchanged/skip), action counts and the masked
        rendered plan in ``plan``. Read-only: computes the plan and
        writes nothing. ``error`` means an unknown profile.
        """
        try:
            return do_preview(
                profile=profile,
                data_dir=data_dir,
                config_dir=config_dir,
                profiles_dir=profiles_dir,
            )
        except Exception as error:  # noqa: BLE001 — tool boundary must not raise
            return _err(error)

    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
