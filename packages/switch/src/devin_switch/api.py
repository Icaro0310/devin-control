"""The stable machine-readable apply contract — what devin-devkit calls.

``devin-devkit apply <profile>`` (separate repo) unifies tool install
and config-profile selection: it resolves the profile in its own
manifest, installs the tool list, then shells out to
``devin-switch apply <resolved-name> [--apply]`` for the config half.
That subcommand always prints the ``devin-switch/apply@1`` JSON envelope
below on stdout — success *and* failure — and exits ``0``/``1``.

``apply_profile()`` is the same contract as a Python callable for
consumers that share an environment; it raises the typed errors
(``ProfileError`` → ``unknown_profile``, ``ManifestError`` →
``invalid_manifest``, ``engine.SnapshotError`` → ``snapshot_failed``)
that the CLI maps onto error envelopes.

The strictness is deliberate and asymmetric with ``use``: the human
path tolerates a malformed ``profile.json`` (the read commands degrade
to empty metadata), but the machine contract fails closed — a caller
that cannot read a profile's metadata must not silently swap config.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devin_switch import engine
from devin_switch import paths as paths_mod
from devin_switch.paths import Roots
from devin_switch.plan import FilePlan, build_plan
from devin_switch.profiles import META_FILENAME, Profile, ProfileError, get

SCHEMA = "devin-switch/apply@1"
ROLLBACK_HINT = "devin-switch rollback --apply"


class ManifestError(ProfileError):
    """``profile.json`` exists but cannot be parsed (fail-closed)."""


def resolve_roots(
    data_dir: Path | None = None,
    config_dir: Path | None = None,
    profiles_dir: Path | None = None,
) -> Roots:
    """Resolve the three roots exactly like the CLI's ``_roots`` —
    ``--data-dir``/``--config-dir``/``--profiles-dir`` overrides over the
    platform defaults; a lone ``data_dir`` puts both stores in one root.
    """
    data = (data_dir or paths_mod.default_data_dir()).expanduser()
    if config_dir is not None:
        config = config_dir.expanduser()
    elif data_dir is not None:
        # single-root mode: one dir holds both data files and User/
        config = data
    else:
        config = paths_mod.default_config_dir()
    return Roots(
        data_dir=data,
        config_dir=config,
        profiles_dir=paths_mod.resolve_profiles_dir(profiles_dir),
    )


def _check_manifest(profile: Profile) -> None:
    """Fail closed when ``profile.json`` exists but does not parse.

    ``profiles._read_meta`` swallows this on the read path (``list`` /
    ``show`` degrade to empty metadata); the apply contract refuses
    instead — a caller deserves to know the profile's declared metadata
    could not be understood before config is swapped under it.
    """
    meta = profile.root / META_FILENAME
    if not meta.is_file():
        return
    try:
        obj = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(
            f"profile {profile.name!r}: cannot parse {META_FILENAME}: {exc}"
        ) from exc
    if not isinstance(obj, dict):
        raise ManifestError(
            f"profile {profile.name!r}: {META_FILENAME} must be a "
            "JSON object"
        )


def _plan_counts(plans: list[FilePlan]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fp in plans:
        counts[fp.action] = counts.get(fp.action, 0) + 1
    return counts


def apply_profile(
    profile: str, roots: Roots, *, apply: bool = False
) -> dict[str, Any]:
    """Preview or apply ``profile`` and return the apply@1 envelope.

    ``apply=False`` is a dry-run: the plan is reported, nothing is
    written — not even the journal. ``apply=True`` runs the same
    snapshot → verify → write → journal path as ``use --apply``.

    Raises ``ProfileError`` (unknown profile — subclass ``ManifestError``
    when ``profile.json`` is malformed) before anything is planned, and
    ``engine.SnapshotError`` before anything is written.
    """
    prof = get(roots.profiles_dir, profile)
    _check_manifest(prof)
    plans = build_plan(prof, roots)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": True,
        "profile": prof.name,
        "dry_run": not apply,
        "actions": _plan_counts(plans),
        "files": [
            {
                "rel": fp.rel,
                "target": str(fp.target),
                "action": fp.action,
                "skip_reason": fp.skip_reason,
            }
            for fp in plans
        ],
        "files_changed": [],
        "skipped": [fp.rel for fp in plans if fp.action == "skip"],
        "backup_dir": None,
        "journal": str(roots.journal_path),
        "rollback": ROLLBACK_HINT,
    }
    if not apply:
        return payload
    result = engine.apply_plan(prof.name, plans, roots)
    payload["files_changed"] = result.files_changed
    payload["backup_dir"] = (
        str(result.backup_dir) if result.backup_dir is not None else None
    )
    return payload


def error_envelope(
    profile: str,
    error_type: str,
    detail: str,
    **extra: Any,
) -> dict[str, Any]:
    """The failure half of the contract — stdout still parses as JSON."""
    return {
        "schema": SCHEMA,
        "ok": False,
        "profile": profile,
        "error_type": error_type,
        "error": detail,
        **extra,
    }
