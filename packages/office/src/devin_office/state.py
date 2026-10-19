"""Writable runtime-state location for devin-office.

State (kanban.json, cmd_queue.json, cmd_inbox/, outbox/, state/, logs)
must not resolve beside the installed module: site-packages may be
read-only, and an upgraded checkout would strand state written by the
old flat layout. Resolution order:

1. ``OFFICE_STATE_DIR`` env override (services/tests pin it).
2. Checkout run whose *flat* layout already holds state — keep it so an
   upgrade does not reopen closed cards or orphan spawned sessions.
3. Checkout run with a writable package dir — historical behavior.
4. Otherwise (installed wheel, or a read-only checkout) — the platform
   state dir (``%LOCALAPPDATA%/devin-office`` on Windows,
   ``$XDG_STATE_HOME/devin-office`` elsewhere).
"""
from __future__ import annotations

import os
from pathlib import Path

_STATE_NAMES = ("kanban.json", "cmd_queue.json", "cmd_inbox", "outbox", "state")


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def _legacy_flat_dir() -> Path:
    """Pre-packaging flat root (packages/office/), whether or not it exists."""
    return _package_dir().parent.parent


def _platform_state_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    else:
        base = Path(
            os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "devin-office"


def _in_checkout() -> bool:
    """True when the package runs from the monorepo (…/packages/office/src/…)."""
    return _legacy_flat_dir().parent.name == "packages"


def state_dir() -> Path:
    env = os.environ.get("OFFICE_STATE_DIR")
    if env:
        return Path(env).expanduser()
    pkg = _package_dir()
    flat = _legacy_flat_dir()
    if _in_checkout():
        if any((flat / n).exists() for n in _STATE_NAMES):
            return flat
        if os.access(pkg, os.W_OK):
            return pkg
    return _platform_state_dir()


def spawn_cwd() -> Path:
    """Default working dir for spawned sessions.

    ``OFFICE_SPAWN_CWD`` wins. Checkout runs keep the historical default
    (the directory containing the repo); an installed package defaults
    to the user's home — never a site-packages/library directory.
    """
    env = os.environ.get("OFFICE_SPAWN_CWD")
    if env:
        return Path(env).expanduser()
    flat = _legacy_flat_dir()
    if _in_checkout():
        return flat.parent.parent.parent
    return Path.home()
