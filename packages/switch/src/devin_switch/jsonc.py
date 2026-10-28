"""Minimal JSONC reader — Devin config files allow ``//`` and ``/* */``
comments and tolerate trailing commas. Same contract as devin-doctor's
``_strip_jsonc`` / ``_load_jsonc``, re-implemented so devin-switch stays
stdlib-only with no cross-package dependency.

Trailing commas are removed only when they close a container — a ``,``
whose next non-whitespace character is ``]`` or ``}``, and that follows
a real value or container close (so ``{,}`` and ``[1,,2]`` stay malformed
and fail loudly). Commas inside string literals are never touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def strip_jsonc(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments while respecting string literals."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return _strip_trailing_commas("".join(out))


def _strip_trailing_commas(text: str) -> str:
    """Drop commas that directly close ``]``/``}``, respecting strings.

    A comma is removable only when (a) its previous significant
    character is a value/closing token — not ``{``, ``[``, ``:``, ``,``
    or start-of-input — and (b) its next non-whitespace character is
    ``]`` or ``}``. Both passes scan character-by-character with the
    same string-literal state machine as the comment stripper, so a
    ``,`` or ``]`` inside ``"…"`` is never misread.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    prev_significant = ""
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            prev_significant = '"'
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if (
                j < n
                and text[j] in "]}"
                and prev_significant not in ("", "{", "[", ":", ",")
            ):
                i += 1  # drop the comma; the closer is emitted next loop
                continue
            out.append(c)
            prev_significant = ","
            i += 1
            continue
        out.append(c)
        if c not in " \t\r\n":
            prev_significant = c
        i += 1
    return "".join(out)


def load_jsonc(path: Path) -> Any:
    return json.loads(strip_jsonc(path.read_text(encoding="utf-8")))


def try_load_json_bytes(data: bytes) -> Any:
    """Parse bytes as JSON/JSONC; return ``None`` when not valid."""
    try:
        return json.loads(strip_jsonc(data.decode("utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
