"""Thin CLI wrapper — all logic lives in the library modules.

- ``devin-switch list`` — discovered profiles (read-only)
- ``devin-switch show <profile>`` — what the profile manages (read-only)
- ``devin-switch diff <a> <b>`` — masked per-file diff between two profiles
- ``devin-switch use <profile>`` — DRY-RUN by default; ``--apply`` writes
- ``devin-switch rollback`` — DRY-RUN by default; ``--apply`` restores the
  last journal backup
- ``devin-switch export <profile> [--out FILE]`` — serialize a profile to
  a portable JSON archive (stdout when ``--out`` is omitted)
- ``devin-switch import <file> [--name NAME] [--force]`` — DRY-RUN by
  default; ``--apply`` creates ``profiles/<name>/`` from a validated
  archive
- ``devin-switch apply <profile>`` — the machine-readable contract:
  same dry-run/``--apply`` semantics as ``use``, but stdout is always
  the ``devin-switch/apply@1`` JSON envelope (success and failure) so
  callers like devin-devkit never scrape human output
- ``devin-switch doctor`` — config sanity + closest-profile ranking

Exit codes: 0 on success / clean dry-run; 1 on any error (unknown profile,
nothing to roll back, snapshot verification failure, invalid archive) or —
for ``doctor`` only — on any FAIL check. ``credentials.toml`` is never
touched, ever.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from devin_switch import api, engine, journal
from devin_switch import paths as paths_mod
from devin_switch.health import check_config_files, rank_profiles
from devin_switch.paths import Roots, target_for
from devin_switch.plan import (
    build_plan,
    profile_diff,
    render_plan,
    summarize_json_top_keys,
)
from devin_switch.profiles import ProfileError, discover, get
from devin_switch.redact import is_credential_file, is_never_touch
from devin_switch.transfer import (
    TransferError,
    build_archive,
    parse_archive,
    render_archive,
    validate_profile_name,
    write_archive_profile,
)


def _add_roots(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Devin data dir (default: platform location — "
        "%%APPDATA%%/devin on Windows, ~/.config/devin elsewhere)",
    )
    p.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Devin UI config dir holding User/ (default: platform "
        "location; falls back to --data-dir when only that is given)",
    )
    p.add_argument(
        "--profiles-dir",
        type=Path,
        default=None,
        help="directory of profile overlays (default: "
        "$DEVIN_SWITCH_PROFILES_DIR, ./profiles, or bundled examples)",
    )


def _add_apply(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--apply",
        action="store_true",
        help="WRITE MODE: perform the switch after a verified snapshot. "
        "Without it the command is a dry-run and writes nothing.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devin-switch",
        description="Switch between Devin configuration profiles (hooks, "
        "MCP, models) with snapshot and verification. Read-only unless "
        "--apply is passed; credentials.toml is never touched.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list discovered profiles")
    _add_roots(p_list)

    p_show = sub.add_parser(
        "show", help="show what a profile manages (values masked)"
    )
    _add_roots(p_show)
    p_show.add_argument("profile")

    p_diff = sub.add_parser(
        "diff", help="masked diff between two profiles (read-only)"
    )
    _add_roots(p_diff)
    p_diff.add_argument("a")
    p_diff.add_argument("b")

    p_use = sub.add_parser(
        "use", help="preview a switch (dry-run) or apply it with --apply"
    )
    _add_roots(p_use)
    _add_apply(p_use)
    p_use.add_argument("profile")

    p_rb = sub.add_parser(
        "rollback",
        help="preview restoring the last backup (dry-run) or restore it "
        "with --apply",
    )
    _add_roots(p_rb)
    _add_apply(p_rb)

    p_exp = sub.add_parser(
        "export",
        help="serialize a profile to a portable JSON archive "
        "(read-only; writes only the --out file)",
    )
    _add_roots(p_exp)
    p_exp.add_argument("profile")
    p_exp.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="FILE",
        help="archive destination (default: stdout; '-' also means "
        "stdout). An existing file is never overwritten.",
    )

    p_imp = sub.add_parser(
        "import",
        help="preview creating a profile from an archive (dry-run) or "
        "create it with --apply",
    )
    _add_roots(p_imp)
    _add_apply(p_imp)
    p_imp.add_argument("file", type=Path, help="archive file to import")
    p_imp.add_argument(
        "--name",
        default=None,
        metavar="NAME",
        help="profile name to create (default: the archive's own name)",
    )
    p_imp.add_argument(
        "--force",
        action="store_true",
        help="replace an existing profile with the same name "
        "(only with --apply)",
    )

    p_apply = sub.add_parser(
        "apply",
        help="apply a profile and print the machine-readable JSON "
        "contract (the devin-devkit handshake); dry-run unless --apply",
    )
    _add_roots(p_apply)
    _add_apply(p_apply)
    p_apply.add_argument("profile")

    p_doc = sub.add_parser(
        "doctor",
        help="config sanity checks + closest-profile ranking (read-only)",
    )
    _add_roots(p_doc)

    return parser


def _roots(args: argparse.Namespace) -> Roots:
    return api.resolve_roots(
        args.data_dir, args.config_dir, args.profiles_dir
    )


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_list(roots: Roots) -> int:
    profiles = discover(roots.profiles_dir)
    if not profiles:
        print(f"no profiles found in {roots.profiles_dir}")
        print("create profiles/<name>/ with config overlays — see README")
        return 0
    print(f"profiles in {roots.profiles_dir}:")
    for profile in profiles.values():
        n = len(profile.files)
        desc = f" — {profile.description}" if profile.description else ""
        print(f"  {profile.name:<16} {n:>2} file(s){desc}")
    return 0


def cmd_show(roots: Roots, name: str) -> int:
    profile = get(roots.profiles_dir, name)
    print(f"profile: {profile.name}")
    print(f"root:    {profile.root}")
    if profile.description:
        print(f"desc:    {profile.description}")
    for note in profile.notes:
        print(f"note:    {note}")
    print("files:")
    for rel in sorted(profile.files):
        src = profile.files[rel]
        size = src.stat().st_size
        if is_never_touch(rel):
            target_desc = "(never managed — skipped)"
        else:
            try:
                target_desc = str(target_for(rel, roots))
            except ValueError as exc:
                target_desc = f"(unsafe — {exc})"
        print(f"  {rel}  [{size} B]")
        print(f"    -> {target_desc}")
        if is_credential_file(rel) or is_never_touch(rel):
            print("    keys: <withheld — credential-carrying file>")
            continue
        keys = summarize_json_top_keys(src.read_bytes())
        if keys:
            print(f"    keys: {', '.join(keys)}")
    return 0


def cmd_diff(roots: Roots, a: str, b: str) -> int:
    profile_a = get(roots.profiles_dir, a)
    profile_b = get(roots.profiles_dir, b)
    print(f"diff {a} -> {b} (values masked):")
    print(profile_diff(profile_a, profile_b))
    return 0


def _plan_counts(plans) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fp in plans:
        counts[fp.action] = counts.get(fp.action, 0) + 1
    return counts


def cmd_use(roots: Roots, name: str, apply: bool) -> int:
    profile = get(roots.profiles_dir, name)
    plans = build_plan(profile, roots)
    counts = _plan_counts(plans)
    summary = ", ".join(
        f"{n} {action}" for action, n in sorted(counts.items())
    )
    if not apply:
        print(f"DRY-RUN use {name} — nothing will be written ({summary})")
        print(render_plan(plans))
        print()
        print("re-run with --apply to snapshot, verify and write")
        return 0
    try:
        result = engine.apply_plan(name, plans, roots)
    except engine.SnapshotError as exc:
        print("ABORTED — snapshot verification failed; no files written",
              file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        print(f"  (backup kept for inspection: {exc.backup_dir})",
              file=sys.stderr)
        return 1
    if result.files_changed:
        print(f"applied profile '{name}' — {len(result.files_changed)} "
              "file(s) written:")
        for rel in result.files_changed:
            print(f"  wrote {rel}")
        print(f"backup: {result.backup_dir}")
    else:
        print(f"profile '{name}' already in effect — nothing to write")
    if result.skipped:
        print(f"skipped (never managed): {', '.join(result.skipped)}")
    print(f"journal: {roots.journal_path}")
    print("undo:    devin-switch rollback --apply")
    return 0


def cmd_rollback(roots: Roots, apply: bool) -> int:
    entry = journal.last_use_entry(roots.journal_path)
    if entry is None:
        print("nothing to roll back — no 'use' entry with an existing "
              "backup in the journal", file=sys.stderr)
        return 1
    try:
        steps = engine.plan_rollback(entry, roots)
    except (FileNotFoundError, engine.SnapshotError, ValueError) as exc:
        print(f"cannot roll back: {exc}", file=sys.stderr)
        return 1
    if not apply:
        print(f"DRY-RUN rollback — would undo 'use {entry.get('profile')}' "
              f"from {entry.get('ts')} (nothing written)")
        for step in steps:
            verb = "restore" if step.action == "restore" else "delete  "
            print(f"  {verb} {step.target}")
        print()
        print("re-run with --apply to restore the backup")
        return 0
    result = engine.apply_rollback(entry, steps, roots)
    print(f"rolled back to pre-'use {result.profile}' state:")
    for step in result.steps:
        verb = "restored" if step.action == "restore" else "deleted "
        print(f"  {verb} {step.target}")
    print(f"journal: {roots.journal_path}")
    return 0


def cmd_export(roots: Roots, name: str, out: Path | None) -> int:
    profile = get(roots.profiles_dir, name)
    result = build_archive(profile)
    payload = render_archive(result.archive)
    for rel in result.skipped:
        print(f"note: {rel} skipped — never managed", file=sys.stderr)
    if result.credential_files:
        print(
            "warning: archive contains credential-named files "
            f"({', '.join(result.credential_files)}) — keep it private",
            file=sys.stderr,
        )
    if out is None or str(out) == "-":
        sys.stdout.write(payload)
        return 0
    out = out.expanduser()
    if out.exists():
        print(
            f"error: {out} already exists — refusing to overwrite",
            file=sys.stderr,
        )
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        # O_EXCL makes "no overwrite" atomic with the create (the exists()
        # check above alone would race); mode 0o600 because the archive
        # can carry credential-named files.
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(
            f"error: {out} already exists — refusing to overwrite",
            file=sys.stderr,
        )
        return 1
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)
    print(
        f"exported profile '{name}' — {result.file_count} file(s) -> {out}"
    )
    return 0


def cmd_import(
    roots: Roots, file: Path, name: str | None, force: bool, apply: bool
) -> int:
    try:
        text = file.expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {file}: {exc}", file=sys.stderr)
        return 1
    archive = parse_archive(text)  # raises TransferError — nothing written
    if name is not None:
        target_name = validate_profile_name(name)
    else:
        # The archive's own name may not be a valid profile name (an
        # exported profile can live under a non-portable dirname) —
        # --name is the escape hatch.
        try:
            target_name = validate_profile_name(archive.name)
        except TransferError as exc:
            raise TransferError(
                f"{exc} — pass --name to import it under a valid "
                "profile name"
            ) from exc
    target = roots.profiles_dir / target_name
    collides = target.exists()
    n = len(archive.files)
    noun = "file" if n == 1 else "files"
    if not apply:
        print(
            f"DRY-RUN import — would create profile '{target_name}' "
            f"({n} {noun}):"
        )
        for f in archive.files:
            print(f"  {f.rel}  [{len(f.data)} B]")
        if archive.description:
            print(f"  profile.json description: {archive.description}")
        if collides:
            print(
                f"warning: profile '{target_name}' already exists — "
                "--apply would fail without --force"
            )
        print()
        print("re-run with --apply to create the profile")
        return 0
    if (
        roots.profiles_dir.resolve()
        == paths_mod.bundled_profiles_dir()
    ):
        print(
            "error: refusing to import into the bundled example profiles "
            "— pass --profiles-dir or set DEVIN_SWITCH_PROFILES_DIR",
            file=sys.stderr,
        )
        return 1
    written = write_archive_profile(
        archive, roots.profiles_dir, target_name, force, roots
    )
    verb = "replaced" if collides else "created"
    print(
        f"imported profile '{target_name}' — {verb} {target} "
        f"({len(written)} file(s)):"
    )
    for rel in written:
        print(f"  wrote {rel}")
    print(f"journal: {roots.journal_path}")
    return 0


def cmd_apply(roots: Roots, name: str, apply: bool) -> int:
    """The devin-devkit handshake: stdout is always one JSON envelope.

    Success and failure both serialize as ``devin-switch/apply@1`` —
    callers check ``ok``/``error_type`` and the exit code, never stderr.
    A human-readable ``error:`` line still goes to stderr on failure so
    the stream isn't silent in logs.
    """
    try:
        payload = api.apply_profile(name, roots, apply=apply)
    except api.ManifestError as exc:
        payload = api.error_envelope(name, "invalid_manifest", str(exc))
    except ProfileError as exc:
        payload = api.error_envelope(name, "unknown_profile", str(exc))
    except engine.SnapshotError as exc:
        payload = api.error_envelope(
            name,
            "snapshot_failed",
            str(exc),
            problems=list(exc.problems),
            backup_dir=str(exc.backup_dir),
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["ok"]:
        print(f"error: {payload['error']}", file=sys.stderr)
        return 1
    return 0


def cmd_doctor(roots: Roots) -> int:
    results = check_config_files(roots)
    worst = 0
    for r in results:
        print(f"{r.status:<4} {r.label} — {r.detail}")
        if r.status == "FAIL":
            worst = 1
    print()
    profiles = discover(roots.profiles_dir)
    if profiles:
        ranked = rank_profiles(profiles, roots)
        best, dist = ranked[0]
        noun = "file" if dist == 1 else "files"
        state = "exactly matches" if dist == 0 else f"{dist} {noun} away"
        print(f"closest profile: {best.name} ({state})")
        for profile, d in ranked[1:]:
            noun = "file" if d == 1 else "files"
            print(f"  {profile.name}: {d} {noun} away")
    else:
        print(f"no profiles in {roots.profiles_dir} — nothing to rank")
    return worst


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    roots = _roots(args)
    try:
        if args.command == "list":
            return cmd_list(roots)
        if args.command == "show":
            return cmd_show(roots, args.profile)
        if args.command == "diff":
            return cmd_diff(roots, args.a, args.b)
        if args.command == "use":
            return cmd_use(roots, args.profile, args.apply)
        if args.command == "rollback":
            return cmd_rollback(roots, args.apply)
        if args.command == "export":
            return cmd_export(roots, args.profile, args.out)
        if args.command == "import":
            return cmd_import(
                roots, args.file, args.name, args.force, args.apply
            )
        if args.command == "apply":
            return cmd_apply(roots, args.profile, args.apply)
        if args.command == "doctor":
            return cmd_doctor(roots)
    except (ProfileError, TransferError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
