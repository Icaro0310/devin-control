"""devin-switch — switch between Devin configuration profiles.

Manages the config files in the Devin config/data dirs (``User/settings.json``,
``config.json`` hooks, ``mcp_config.json`` MCP entries) as declarative
profiles, with verified snapshots before any write and a rollback journal.

``credentials.toml`` is never touched — not even read.
"""

from __future__ import annotations

from devin_switch.api import apply_profile

__all__ = ["apply_profile"]
__version__ = "0.1.1"
