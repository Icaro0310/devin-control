"""Trigger-manifest input for the fan-out planner.

The PAS session-dispatcher consumes declarative trigger manifests from
``.devin/catalog/triggers/*.{yaml,yml,json}`` (devin-skill-catalog §8):
``after`` declares ORDER edges, ``requires.artifacts`` declares DATA
edges to the producing handler. This module reads the same format as
plan input: each enabled manifest becomes one work unit, resolved into
DAG order before the planner decides fan-out.

YAML support is a strict stdlib subset — the package ships with zero
dependencies. The subset covers the manifest grammar: block maps, block
sequences of scalars, inline ``[a, b]`` lists, comments, and quoted or
bare scalars. Anything outside it (anchors, aliases, tags, block
scalars, inline maps, multi-document markers, sequence items that are
not scalars) fails loudly instead of being silently misparsed.

The data-only contract still holds: only manifest *metadata* (id, event,
resolved dependencies) reaches the spec and the plan. ``action.command``
is parsed for structure but never propagated.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devin_orchestrator.planner import WorkerPlan, plan_task

TRIGGER_SUFFIXES = {".yaml", ".yml", ".json"}

# trigger.event values the dispatcher treats as synthetic/scheduled; for
# planning purposes any non-empty string is accepted — the DAG does not
# interpret events.


# ------------------------------------------------------- yaml subset


@dataclass
class _Line:
    no: int
    indent: int
    text: str


_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?$")
_BAD_BARE_VALUE = re.compile(r":\s")


def _strip_comment(raw: str) -> str:
    """Cut a trailing ``#`` comment, honouring single/double quotes."""
    in_s = in_d = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if in_s:
            if ch == "'":
                in_s = False
        elif in_d:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_d = False
        else:
            if ch == "'":
                in_s = True
            elif ch == '"':
                in_d = True
            elif ch == "#" and (i == 0 or raw[i - 1] in " \t"):
                return raw[:i]
        i += 1
    return raw


def _tokenize(text: str, source: str) -> list[_Line]:
    lines: list[_Line] = []
    for no, raw in enumerate(text.splitlines(), 1):
        indent_raw = raw[: len(raw) - len(raw.lstrip())]
        if "\t" in indent_raw:
            raise ValueError(f"{source}:{no}: tab indentation is not supported")
        content = _strip_comment(raw).rstrip()
        if not content.strip():
            continue
        stripped = content.strip()
        if stripped in {"---", "..."}:
            raise ValueError(
                f"{source}:{no}: document markers are outside the supported "
                "YAML subset")
        indent = len(content) - len(content.lstrip(" "))
        lines.append(_Line(no, indent, stripped))
    return lines


def _split_key(text: str, source: str, no: int) -> tuple[str, str]:
    """Split ``key: value`` on the first ':' followed by space or EOL."""
    if text[0] in "'\"":
        raise ValueError(f"{source}:{no}: quoted keys are not supported")
    for i, ch in enumerate(text):
        if ch == ":" and (i + 1 == len(text) or text[i + 1] == " "):
            key = text[:i].strip()
            if not key:
                raise ValueError(f"{source}:{no}: empty key")
            return key, text[i + 1:].strip()
    raise ValueError(f"{source}:{no}: expected 'key: value' or '- item'")


def _unescape_double(text: str, source: str, no: int) -> str:
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 1
            if i >= len(text):
                break
            esc = text[i]
            mapping = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "/": "/"}
            if esc not in mapping:
                raise ValueError(
                    f"{source}:{no}: unsupported escape '\\{esc}' in "
                    "double-quoted scalar")
            out.append(mapping[esc])
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _parse_scalar(text: str, source: str, no: int) -> Any:
    c0 = text[0]
    if c0 in "|>":
        raise ValueError(
            f"{source}:{no}: block scalars ('{c0}') are outside the "
            "supported YAML subset")
    if c0 in "&*!%@`":
        raise ValueError(
            f"{source}:{no}: '{text[:1]}' constructs (anchors/aliases/tags) "
            "are outside the supported YAML subset")
    if c0 == "{":
        raise ValueError(
            f"{source}:{no}: inline maps are outside the supported subset")
    if text == "- " or text.startswith("- "):
        raise ValueError(f"{source}:{no}: malformed scalar {text!r}")
    if c0 == "[":
        if not text.endswith("]"):
            raise ValueError(f"{source}:{no}: unterminated inline list")
        inner = text[1:-1].strip()
        if not inner:
            return []
        parts, depth, in_s, in_d, start = [], 0, False, False, 0
        for i, ch in enumerate(inner):
            if in_s:
                if ch == "'":
                    in_s = False
            elif in_d:
                if ch == '"':
                    in_d = False
            elif ch == "'":
                in_s = True
            elif ch == '"':
                in_d = True
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(inner[start:i])
                start = i + 1
        parts.append(inner[start:])
        return [
            _parse_scalar(p.strip(), source, no)
            for p in parts if p.strip()
        ]
    if c0 == "'":
        if len(text) < 2 or not text.endswith("'"):
            raise ValueError(f"{source}:{no}: unterminated quoted scalar")
        body = text[1:-1]
        # '' is the only single-quote escape; a lone ' is a syntax error.
        rebuilt = []
        i = 0
        while i < len(body):
            if body[i] == "'":
                if i + 1 < len(body) and body[i + 1] == "'":
                    rebuilt.append("'")
                    i += 2
                    continue
                raise ValueError(
                    f"{source}:{no}: unescaped quote in single-quoted scalar")
            rebuilt.append(body[i])
            i += 1
        return "".join(rebuilt)
    if c0 == '"':
        if len(text) < 2 or not text.endswith('"') or (
            len(text) >= 2 and text[-2] == "\\"
        ):
            raise ValueError(f"{source}:{no}: unterminated quoted scalar")
        return _unescape_double(text[1:-1], source, no)
    if _BAD_BARE_VALUE.search(text):
        raise ValueError(
            f"{source}:{no}: bare scalar contains ': ' — quote the value")
    low = text.lower()
    if low in {"null", "~"}:
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _is_seq_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _parse_seq(lines: list[_Line], pos: int, indent: int,
               source: str) -> tuple[list[Any], int]:
    items: list[Any] = []
    while pos < len(lines):
        ln = lines[pos]
        if ln.indent != indent or not _is_seq_item(ln.text):
            break
        item = ln.text[1:].strip()
        items.append(None if not item else
                     _parse_scalar(item, source, ln.no))
        pos += 1
    return items, pos


def _parse_map(lines: list[_Line], pos: int, indent: int,
               source: str) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while pos < len(lines):
        ln = lines[pos]
        if ln.indent < indent:
            break
        if ln.indent > indent:
            raise ValueError(f"{source}:{ln.no}: unexpected indentation")
        if _is_seq_item(ln.text):
            break  # sequence at map level — caller reports it
        key, val = _split_key(ln.text, source, ln.no)
        if key in out:
            raise ValueError(f"{source}:{ln.no}: duplicate key {key!r}")
        pos += 1
        if val:
            out[key] = _parse_scalar(val, source, ln.no)
        elif pos < len(lines) and lines[pos].indent > indent:
            out[key], pos = _parse_block(
                lines, pos, lines[pos].indent, source)
        elif (
            pos < len(lines)
            and lines[pos].indent == indent
            and _is_seq_item(lines[pos].text)
        ):
            # YAML allows a block sequence at the key's own indent.
            out[key], pos = _parse_seq(lines, pos, indent, source)
        else:
            out[key] = None
    return out, pos


def _parse_block(lines: list[_Line], pos: int, indent: int,
                 source: str) -> tuple[Any, int]:
    if _is_seq_item(lines[pos].text):
        return _parse_seq(lines, pos, indent, source)
    return _parse_map(lines, pos, indent, source)


def parse_yaml_subset(text: str, source: str = "<yaml>") -> Any:
    """Parse the strict YAML subset used by trigger manifests.

    Returns the decoded object (manifests decode to ``dict``). Raises
    ``ValueError`` on any construct outside the subset — parse failures
    are always loud, never a silent wrong shape.
    """
    lines = _tokenize(text, source)
    if not lines:
        return None
    value, pos = _parse_block(lines, 0, lines[0].indent, source)
    if pos != len(lines):
        ln = lines[pos]
        raise ValueError(f"{source}:{ln.no}: unexpected content {ln.text!r}")
    return value


# ------------------------------------------------------- loading


def load_triggers_dir(path: str | Path) -> list[dict]:
    """Load every ``*.{yaml,yml,json}`` manifest in a triggers dir.

    Each manifest is a ``dict`` with a non-empty string ``id``; the
    loader adds ``_source`` (the filename, for error messages). Parse
    errors, non-map documents and id-less manifests raise ``ValueError``
    — a triggers dir with problems is rejected whole, never partially
    consumed.
    """
    d = Path(path)
    if not d.is_dir():
        raise ValueError(f"triggers dir not found: {d}")
    files = sorted(
        f for f in d.iterdir()
        if f.is_file() and f.suffix.lower() in TRIGGER_SUFFIXES)
    if not files:
        raise ValueError(
            f"no trigger manifests (*{', *'.join(sorted(TRIGGER_SUFFIXES))}) "
            f"in {d}")
    manifests: list[dict] = []
    for f in files:
        try:
            if f.suffix.lower() == ".json":
                data = json.loads(f.read_text(encoding="utf-8"))
            else:
                data = parse_yaml_subset(f.read_text(encoding="utf-8"),
                                         str(f))
        except (ValueError, OSError) as exc:
            raise ValueError(f"{f.name}: {exc}") from exc
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(  # noqa: TRY004 - callers catch ValueError
                    f"{f.name}: manifest must be a mapping")
            mid = item.get("id")
            if not isinstance(mid, str) or not mid.strip():
                raise ValueError(
                    f"{f.name}: manifest requires a non-empty 'id'")
            item = dict(item)
            item["_source"] = f.name
            manifests.append(item)
    return manifests


# ------------------------------------------------------- dag


def _list_of_str(m: dict, key: str) -> list[str]:
    """Read a manifest list field; wrong shape is a load-time error."""
    val = m.get(key)
    if val is None:
        return []
    if not isinstance(val, list) or not all(isinstance(v, str) and v
                                            for v in val):
        raise ValueError(
            f"{m.get('id', '?')}: '{key}' must be a list of non-empty "
            "strings")
    return list(val)


def _dag_edges(manifests: list[dict]) -> dict[str, set[str]]:
    """producer→consumer edges: ``after`` (order) + ``requires.artifacts``
    (data), matching the session-dispatcher's DAG semantics."""
    produced_by: dict[str, str] = {}
    for m in manifests:
        if not m.get("enabled", True):
            continue
        arts = m.get("artifacts")
        if isinstance(arts, dict) and isinstance(arts.get("output"), str) \
                and arts["output"]:
            produced_by[arts["output"]] = m["id"]
    edges: dict[str, set[str]] = {}
    for m in manifests:
        mid = m["id"]
        deps = set(_list_of_str(m, "after"))
        reqs = m.get("requires")
        if reqs is not None:
            if not isinstance(reqs, dict):
                raise ValueError(f"{mid}: 'requires' must be a mapping")
            for art in _list_of_str(reqs, "artifacts"):
                producer = produced_by.get(art)
                if producer and producer != mid:
                    deps.add(producer)
        edges[mid] = deps
    return edges


def _check_cycles(edges: dict[str, set[str]]) -> None:
    """DFS cycle check; raises ValueError naming the cycle path."""
    color = {n: 0 for n in edges}  # 0 white, 1 gray, 2 black
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for dep in sorted(edges.get(node, ())):
            if dep not in color:
                continue
            if color[dep] == 1:
                cycle = stack[stack.index(dep):] + [dep]
                raise ValueError("cycle in DAG: " + " -> ".join(cycle))
            if color[dep] == 0:
                visit(dep)
        stack.pop()
        color[node] = 2

    for n in sorted(edges):
        if color[n] == 0:
            visit(n)


def resolve_dag(
    manifests: list[dict],
    event: str | None = None,
) -> tuple[list[dict], list[str]]:
    """Validate the manifest set and return enabled manifests in DAG order.

    ``event`` restricts the unit set to manifests whose
    ``trigger.event`` matches (the dispatcher's per-event view). Returns
    ``(ordered, warnings)``; raises ``ValueError`` on duplicate ids,
    dangling ``after``/``requires`` references, or any dependency cycle.
    """
    ids = [m["id"] for m in manifests]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate manifest id: {dup}")
    by_id = {m["id"]: m for m in manifests}
    enabled_ids = {i for i, m in by_id.items() if m.get("enabled", True)}

    produced = {
        m["artifacts"]["output"]
        for m in manifests
        if m.get("enabled", True)
        and isinstance(m.get("artifacts"), dict)
        and m["artifacts"].get("output")
    }
    warnings: list[str] = []
    for m in manifests:
        mid = m["id"]
        for dep in _list_of_str(m, "after"):
            if dep not in by_id:
                raise ValueError(
                    f"{mid}: after '{dep}' — no such manifest in the "
                    "triggers dir")
            if dep not in enabled_ids:
                warnings.append(
                    f"{mid}: after '{dep}' is disabled — ordering edge "
                    "ignored")
        reqs = m.get("requires")
        if reqs is not None:
            if not isinstance(reqs, dict):
                raise ValueError(f"{mid}: 'requires' must be a mapping")
            for art in _list_of_str(reqs, "artifacts"):
                if art not in produced:
                    raise ValueError(
                        f"{mid}: requires artifact '{art}' — no enabled "
                        "manifest produces it")

    edges = _dag_edges(manifests)
    _check_cycles(edges)

    if event is None:
        subset = [m for m in manifests if m.get("enabled", True)]
    else:
        subset = [
            m for m in manifests
            if m.get("enabled", True)
            and isinstance(m.get("trigger"), dict)
            and m["trigger"].get("event") == event
        ]
    subset_ids = {m["id"] for m in subset}
    deps = {m["id"]: edges.get(m["id"], set()) & subset_ids for m in subset}

    remaining = dict(deps)
    order: list[str] = []
    while remaining:
        ready = sorted(
            n for n, d in remaining.items() if not d & set(remaining))
        if not ready:
            raise ValueError(
                "cycle in DAG among: " + ", ".join(sorted(remaining)))
        for n in ready:
            order.append(n)
            del remaining[n]
    by_id2 = {m["id"]: m for m in subset}
    return [by_id2[i] for i in order], warnings


# ------------------------------------------------------- spec + plan


def spec_from_triggers(
    path: str | Path,
    event: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Resolve a triggers dir into ``(spec, units, warnings)``.

    ``spec`` feeds ``planner.plan_task``; ``units`` is the DAG-ordered
    unit list attached to the plan; ``warnings`` are DAG-level
    observations merged into ``plan.warnings``. Each enabled manifest is
    one independent unit — the dispatcher's ``after``/``requires`` edges
    fix the order, the planner fixes the width.
    """
    manifests = load_triggers_dir(path)
    ordered, warnings = resolve_dag(manifests, event)
    edges = _dag_edges(ordered)
    unit_ids = {m["id"] for m in ordered}
    units = [
        {
            "id": m["id"],
            "event": (m.get("trigger") or {}).get("event"),
            "after": sorted(edges.get(m["id"], set()) & unit_ids),
        }
        for m in ordered
    ]
    # A dir of pure observers stays read-only; anything that mutates
    # (gate/corrector/enricher, or an undeclared class) needs writes.
    needs_write = any(m.get("class") != "observer" for m in ordered)
    spec = {
        "kind": "implementation",
        "estimated_scope": "medium",
        "independent_units": len(ordered),
        "needs_write": needs_write,
        "summary": f"{len(ordered)} trigger manifest(s) as a fan-out DAG",
        "units_detail": [{"id": u["id"]} for u in units] or None,
    }
    return spec, units, warnings


def plan_from_triggers(
    path: str | Path,
    event: str | None = None,
    env: dict[str, str] | None = None,
    signals: dict[str, Any] | None = None,
) -> WorkerPlan:
    """Plan fan-out over a triggers dir: load → resolve DAG → plan_task.

    All planner contracts apply unchanged: ``ram_pressure`` still caps
    workers, ``DEVIN_INSIDE_SUBAGENT`` still blocks nesting, and a
    fanned-out plan still carries ``collect=True``.
    """
    spec, units, warnings = spec_from_triggers(path, event)
    plan = plan_task(spec, env=env, signals=signals)
    plan.units = units
    plan.warnings = warnings + plan.warnings
    if not units:
        plan.warnings.insert(
            0, "no enabled trigger manifests — 0 work units")
    return plan
