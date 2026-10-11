"""Portable profile archives — ``export`` serializes a profile to a
single JSON document, ``import`` materializes one back under
``profiles/<name>/``.

Archive format (schema version 1)::

    {
      "format": "devin-switch/profile-archive",
      "schema_version": 1,
      "name": "<profile>",
      "description": "...",           # optional, from profile.json
      "notes": ["..."],               # optional, from profile.json
      "exported_at": "<utc iso>",
      "files": [
        {"path": "User/settings.json",
         "sha256": "<hex>",
         "data": "<base64>"}
      ]
    }

Why JSON and not tar/zip: everything else in the package is auditable
JSON (backup manifests, the journal, ``profile.json``), the stdlib has no
binary archive story that stays this small, and a single diffable
document is easier to eyeball before ``import``.

Import is fail-closed: unknown ``format``/``schema_version``, malformed
JSON, sha256 mismatches, duplicate entries and unsafe paths are all
rejected before a single byte is written. ``credentials.toml`` is never
exported and never accepted back — the same never-touch rule as
``use``/``rollback``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from devin_switch import journal
from devin_switch.paths import Roots
from devin_switch.profiles import META_FILENAME, Profile
from devin_switch.redact import is_credential_file, is_never_touch

ARCHIVE_FORMAT = "devin-switch/profile-archive"
SCHEMA_VERSION = 1

_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")


class TransferError(Exception):
    """Malformed archive, unsafe path, name collision — nothing written."""


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def validate_profile_name(name: str) -> str:
    """Profile names become directory names — restrict to a portable,
    non-hidden, single-component token."""
    if not isinstance(name, str) or not _PROFILE_NAME.match(name):
        raise TransferError(
            f"invalid profile name {name!r} — expected "
            "[A-Za-z0-9._-]+ not starting with '.'"
        )
    return name


def validate_rel_path(rel: Any) -> str:
    """A payload path must be a normalized relative posix path that maps
    to a managed profile file — no ``..``, no absolute paths, no
    backslashes, no hidden/``__pycache__`` components, no
    ``profile.json`` and never ``credentials.toml``."""
    problems = "must be a relative posix path without '..', '\\', hidden or __pycache__ parts"
    if not isinstance(rel, str) or not rel:
        raise TransferError(f"archive path {rel!r}: {problems}")
    pure = PurePosixPath(rel)
    parts = pure.parts
    if (
        pure.is_absolute()
        or "\\" in rel
        or "\x00" in rel
        or ".." in parts
        or pure.as_posix() != rel  # collapses //, ./, trailing /
        or rel.endswith("/")
    ):
        raise TransferError(f"archive path {rel!r}: {problems}")
    name = parts[-1]
    if (
        name.startswith(".")
        or name == META_FILENAME
        or "__pycache__" in parts
    ):
        raise TransferError(
            f"archive path {rel!r}: not a managed profile file"
        )
    if is_never_touch(rel):
        raise TransferError(
            f"archive path {rel!r}: credentials.toml is never managed — "
            "refusing to import it"
        )
    return rel


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@dataclass
class ExportResult:
    name: str
    archive: dict[str, Any]
    file_count: int
    skipped: list[str] = field(default_factory=list)  # never-touch files
    credential_files: list[str] = field(default_factory=list)


def build_archive(profile: Profile) -> ExportResult:
    """Serialize every managed file of ``profile`` — verbatim bytes as
    base64 plus a sha256 for integrity on import.

    ``credentials.toml`` is skipped without being read (never-touch);
    credential-*named* files are exported (the user asked for a portable
    copy) but reported so the CLI can warn that the archive carries
    secrets in the clear.
    """
    files: list[dict[str, Any]] = []
    skipped: list[str] = []
    credential_files: list[str] = []
    for rel in sorted(profile.files):
        if is_never_touch(rel):
            skipped.append(rel)
            continue
        data = profile.file_bytes(rel)
        files.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": base64.b64encode(data).decode("ascii"),
            }
        )
        if is_credential_file(rel):
            credential_files.append(rel)
    archive: dict[str, Any] = {
        "format": ARCHIVE_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "name": profile.name,
    }
    if profile.description:
        archive["description"] = profile.description
    if profile.notes:
        archive["notes"] = list(profile.notes)
    archive["exported_at"] = journal.journal_timestamp()
    archive["files"] = files
    return ExportResult(
        profile.name,
        archive,
        len(files),
        skipped=skipped,
        credential_files=credential_files,
    )


def render_archive(archive: dict[str, Any]) -> str:
    return json.dumps(archive, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# import — parse + validate (no writes)
# ---------------------------------------------------------------------------


@dataclass
class ArchiveFile:
    rel: str
    data: bytes


@dataclass
class Archive:
    name: str
    description: str
    notes: list[str]
    files: list[ArchiveFile]


def _expect_str(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise TransferError(f"archive field {key!r} must be a string")
    return value


def parse_archive(text: str) -> Archive:
    """Validate an archive document end to end. Any deviation —
    unparseable JSON, wrong ``format``/``schema_version``, bad field
    types, undecodable base64, sha256 mismatch, unsafe or duplicate
    paths — raises ``TransferError`` before anything is written."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TransferError(f"not a JSON archive: {exc}") from exc
    if not isinstance(obj, dict):
        raise TransferError("archive is not a JSON object")
    if obj.get("format") != ARCHIVE_FORMAT:
        raise TransferError(
            f"unrecognized archive format {obj.get('format')!r} — "
            f"expected {ARCHIVE_FORMAT!r}"
        )
    version = obj.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or (
        version != SCHEMA_VERSION
    ):
        raise TransferError(
            f"unsupported schema_version {version!r} — this devin-switch "
            f"only reads version {SCHEMA_VERSION}"
        )
    name = validate_profile_name(_expect_str(obj, "name"))

    description = obj.get("description", "")
    if not isinstance(description, str):
        raise TransferError("archive field 'description' must be a string")
    notes = obj.get("notes", [])
    if not isinstance(notes, list) or not all(
        isinstance(n, str) for n in notes
    ):
        raise TransferError(
            "archive field 'notes' must be a list of strings"
        )

    raw_files = obj.get("files")
    if not isinstance(raw_files, list):
        raise TransferError("archive field 'files' must be a list")
    files: list[ArchiveFile] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw_files):
        where = f"files[{i}]"
        if not isinstance(entry, dict):
            raise TransferError(f"archive {where} must be an object")
        rel = validate_rel_path(entry.get("path"))
        if rel in seen:
            raise TransferError(f"duplicate archive path {rel!r}")
        seen.add(rel)
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not _SHA256_HEX.match(digest):
            raise TransferError(
                f"archive {where} ('{rel}'): 'sha256' must be 64 hex chars"
            )
        b64 = entry.get("data")
        if not isinstance(b64, str):
            raise TransferError(
                f"archive {where} ('{rel}'): 'data' must be base64 text"
            )
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TransferError(
                f"archive {where} ('{rel}'): invalid base64 data"
            ) from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise TransferError(
                f"archive {where} ('{rel}'): sha256 mismatch — "
                "archive corrupted or tampered"
            )
        files.append(ArchiveFile(rel, data))
    return Archive(name, description, list(notes), files)


# ---------------------------------------------------------------------------
# import — write path (validated archive only)
# ---------------------------------------------------------------------------


def _atomic_write_in(dirpath: Path, rel: str, data: bytes) -> None:
    """Write ``dirpath/rel`` via sibling tmp + ``os.replace``. ``rel``
    comes pre-validated so it can never escape ``dirpath``."""
    target = dirpath.joinpath(*PurePosixPath(rel).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".devin-switch.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def write_archive_profile(
    archive: Archive,
    profiles_dir: Path,
    name: str,
    force: bool,
    roots: Roots,
) -> list[str]:
    """Materialize the archive as ``profiles_dir/<name>``.

    Writes into a fresh sibling dir first, then renames it into place —
    a profile only appears under its name when it is complete. With
    ``force`` an existing profile dir is replaced entirely. Appends an
    ``import`` journal entry. Returns the written relative paths.
    """
    target = profiles_dir / name
    if target.exists() and not force:
        raise TransferError(
            f"profile {name!r} already exists — re-run with --force to "
            "replace it"
        )
    staging = profiles_dir / f".{name}.import-{uuid.uuid4().hex[:12]}"
    staging.mkdir(parents=True, exist_ok=False)
    written: list[str] = []
    try:
        for f in archive.files:
            _atomic_write_in(staging, f.rel, f.data)
            written.append(f.rel)
        meta: dict[str, Any] = {}
        if archive.description:
            meta["description"] = archive.description
        if archive.notes:
            meta["notes"] = list(archive.notes)
        if meta:
            (staging / META_FILENAME).write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        # Re-check just before the rename: a profile dir must never be
        # clobbered without force, even if it appeared mid-import.
        if target.exists():
            if not force:
                raise TransferError(
                    f"profile {name!r} already exists — re-run with "
                    "--force to replace it"
                )
            shutil.rmtree(target)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    journal.append(roots.journal_path, "import", name, written, None)
    return written
