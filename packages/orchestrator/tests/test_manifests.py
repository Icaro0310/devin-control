"""Trigger-manifest consumption: load a triggers dir, resolve its DAG,
plan fan-out over the DAG-ordered units (plan --from-triggers)."""

import json

import pytest
from devin_orchestrator.cli import main
from devin_orchestrator.manifests import (
    load_triggers_dir,
    parse_yaml_subset,
    plan_from_triggers,
    resolve_dag,
    spec_from_triggers,
)


def _write(dir_path, name, text):
    f = dir_path / name
    f.write_text(text, encoding="utf-8")
    return f


def _manifest(mid, event="Scheduled", cls="corrector", after=None,
              extra=""):
    after_yaml = ""
    if after:
        after_yaml = "\nafter:\n" + "".join(f"  - {d}\n" for d in after)
    return (
        f"id: {mid}\n"
        "version: 1\n"
        "owner: tests\n"
        "enabled: true\n"
        "severity: p2\n"
        f"class: {cls}\n"
        "trigger:\n"
        f"  event: {event}\n"
        "  scope: workspace\n"
        "action:\n"
        "  command: 'python {root}/scripts/x.py'\n"
        "  mode: sync\n"
        "  timeout: 60\n"
        f"{after_yaml}{extra}")


# -- yaml subset parser ----------------------------------------------------


def test_yaml_subset_scalars_and_nesting():
    doc = parse_yaml_subset(
        "# comment\n"
        "id: a.b\n"
        "version: 2\n"
        "enabled: false\n"
        "timeout: 1.5\n"
        "nothing: null\n"
        "platforms: [windows, linux]\n"
        "trigger:\n"
        "  event: Scheduled\n"
        "  schedule: 'daily 05:30'\n"
        "after:\n"
        "- dep.one   # non-indented sequence is legal YAML\n"
        "- 'dep.two'\n")
    assert doc["id"] == "a.b"
    assert doc["version"] == 2
    assert doc["enabled"] is False
    assert doc["timeout"] == 1.5
    assert doc["nothing"] is None
    assert doc["platforms"] == ["windows", "linux"]
    assert doc["trigger"] == {"event": "Scheduled", "schedule": "daily 05:30"}
    assert doc["after"] == ["dep.one", "dep.two"]


def test_yaml_subset_rejects_unsupported_constructs():
    for bad in [
        "a: |n  block\n",          # block scalar
        "a: &anc x\n",             # anchor
        "a: *alias\n",             # alias
        "a: {k: v}\n",             # inline map
        "---\na: 1\n",             # document marker
        "a: 'unterminated\n",
        'a: "bad \\q escape"\n',
        "a: 1\na: 2\n",            # duplicate key
        "a:\n  b: 1\n c: 2\n",     # dedent to unopened level
        "a: b: c\n",               # ': ' inside bare scalar
    ]:
        with pytest.raises(ValueError):
            parse_yaml_subset(bad)


def test_yaml_empty_doc_returns_none():
    assert parse_yaml_subset("# only a comment\n\n") is None


# -- loader ----------------------------------------------------------------


def test_load_triggers_dir_reads_yaml_and_json(tmp_path):
    _write(tmp_path, "a.yaml", _manifest("a.one"))
    _write(tmp_path, "b.json", json.dumps({"id": "b.two"}))
    _write(tmp_path, "ignored.txt", "id: nope\n")
    manifests = load_triggers_dir(tmp_path)
    assert [m["id"] for m in manifests] == ["a.one", "b.two"]
    assert manifests[0]["_source"] == "a.yaml"


def test_load_triggers_dir_missing_and_empty(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_triggers_dir(tmp_path / "nope")
    with pytest.raises(ValueError, match="no trigger manifests"):
        load_triggers_dir(tmp_path)


def test_load_triggers_dir_malformed_yaml_fails_loudly(tmp_path):
    _write(tmp_path, "ok.yaml", _manifest("ok.one"))
    _write(tmp_path, "bad.yaml", "id: bad.one\n\tbroken: true\n")
    with pytest.raises(ValueError, match="bad.yaml"):
        load_triggers_dir(tmp_path)


def test_load_triggers_dir_requires_id(tmp_path):
    _write(tmp_path, "noid.yaml", "version: 1\ntrigger:\n  event: X\n")
    with pytest.raises(ValueError, match="requires a non-empty 'id'"):
        load_triggers_dir(tmp_path)


# -- dag resolution ---------------------------------------------------------


def test_resolve_dag_linear_order(tmp_path):
    _write(tmp_path, "a.yaml", _manifest("a.first"))
    _write(tmp_path, "b.yaml", _manifest("b.second", after=["a.first"]))
    _write(tmp_path, "c.yaml", _manifest("c.third", after=["b.second"]))
    ordered, warnings = resolve_dag(load_triggers_dir(tmp_path))
    assert [m["id"] for m in ordered] == ["a.first", "b.second", "c.third"]
    assert warnings == []


def test_resolve_dag_diamond(tmp_path):
    _write(tmp_path, "root.yaml", _manifest("x.root"))
    _write(tmp_path, "l.yaml", _manifest("x.left", after=["x.root"]))
    _write(tmp_path, "r.yaml", _manifest("x.right", after=["x.root"]))
    _write(tmp_path, "join.yaml",
           _manifest("x.join", after=["x.left", "x.right"]))
    ordered, _ = resolve_dag(load_triggers_dir(tmp_path))
    ids = [m["id"] for m in ordered]
    assert ids[0] == "x.root" and ids[-1] == "x.join"
    assert set(ids[1:3]) == {"x.left", "x.right"}


def test_resolve_dag_cycle_fails_with_path(tmp_path):
    _write(tmp_path, "a.yaml", _manifest("c.a", after=["c.c"]))
    _write(tmp_path, "b.yaml", _manifest("c.b", after=["c.a"]))
    _write(tmp_path, "c.yaml", _manifest("c.c", after=["c.b"]))
    with pytest.raises(ValueError, match="cycle in DAG: .*->"):
        resolve_dag(load_triggers_dir(tmp_path))


def test_resolve_dag_missing_dependency_fails(tmp_path):
    _write(tmp_path, "a.yaml", _manifest("m.a", after=["m.ghost"]))
    with pytest.raises(ValueError, match="after 'm.ghost'"):
        resolve_dag(load_triggers_dir(tmp_path))


def test_resolve_dag_requires_artifacts_edge(tmp_path):
    _write(tmp_path, "prod.yaml",
           _manifest("p.producer", extra="artifacts:\n  output: notes\n"))
    _write(tmp_path, "cons.yaml",
           _manifest("p.consumer",
                     extra="requires:\n  artifacts:\n    - notes\n"))
    ordered, _ = resolve_dag(load_triggers_dir(tmp_path))
    assert [m["id"] for m in ordered] == ["p.producer", "p.consumer"]


def test_resolve_dag_requires_missing_artifact_fails(tmp_path):
    _write(tmp_path, "cons.yaml",
           _manifest("p.consumer",
                     extra="requires:\n  artifacts:\n    - ghost\n"))
    with pytest.raises(ValueError, match="requires artifact 'ghost'"):
        resolve_dag(load_triggers_dir(tmp_path))


def test_resolve_dag_disabled_manifests_and_deps(tmp_path):
    _write(tmp_path, "off.yaml",
           _manifest("d.off").replace("enabled: true", "enabled: false"))
    _write(tmp_path, "on.yaml", _manifest("d.on", after=["d.off"]))
    ordered, warnings = resolve_dag(load_triggers_dir(tmp_path))
    assert [m["id"] for m in ordered] == ["d.on"]
    assert any("disabled" in w for w in warnings)


def test_resolve_dag_event_filter(tmp_path):
    _write(tmp_path, "a.yaml", _manifest("e.sched", event="Scheduled"))
    _write(tmp_path, "b.yaml",
           _manifest("e.end", event="SessionEnd", after=["e.sched"]))
    ordered, _ = resolve_dag(load_triggers_dir(tmp_path), event="SessionEnd")
    assert [m["id"] for m in ordered] == ["e.end"]


# -- plan integration -------------------------------------------------------


def test_plan_from_triggers_fans_out_in_dag_order(tmp_path):
    for i in range(4):
        _write(tmp_path, f"u{i}.yaml", _manifest(f"t.unit-{i}"))
    plan = plan_from_triggers(tmp_path)
    assert plan.workers == 3  # MAX_WORKERS cap over 4 units
    assert plan.collect is True
    assert [u["id"] for u in plan.units] == [
        "t.unit-0", "t.unit-1", "t.unit-2", "t.unit-3"]
    assert plan.units[3]["after"] == []


def test_plan_from_triggers_propagates_resolved_edges(tmp_path):
    _write(tmp_path, "a.yaml", _manifest("t.a"))
    _write(tmp_path, "b.yaml", _manifest("t.b", after=["t.a"]))
    plan = plan_from_triggers(tmp_path)
    assert plan.units[1] == {
        "id": "t.b", "event": "Scheduled", "after": ["t.a"]}


def test_plan_from_triggers_observers_stay_readonly(tmp_path):
    _write(tmp_path, "a.yaml", _manifest("o.a", cls="observer"))
    _write(tmp_path, "b.yaml", _manifest("o.b", cls="observer"))
    plan = plan_from_triggers(tmp_path)
    assert plan.workers == 2
    assert plan.profiles == ["subagent_explore"] * 2


def test_plan_from_triggers_honours_existing_contracts(tmp_path):
    for i in range(3):
        _write(tmp_path, f"u{i}.yaml", _manifest(f"t.u{i}"))
    # ram_pressure still caps fan-out at one worker
    pressured = plan_from_triggers(
        tmp_path, signals={"ram_pressure": True, "ram_available_mb": 800})
    assert pressured.workers == 1
    assert any("ram_pressure" in w for w in pressured.warnings)
    # inside a worker, nested fan-out is still forbidden
    nested = plan_from_triggers(tmp_path, env={"DEVIN_INSIDE_SUBAGENT": "1"})
    assert nested.workers == 0
    assert nested.nesting_blocked is True


def test_plan_from_triggers_all_disabled_warns(tmp_path):
    _write(tmp_path, "off.yaml",
           _manifest("d.off").replace("enabled: true", "enabled: false"))
    plan = plan_from_triggers(tmp_path)
    assert plan.workers == 0
    assert plan.units == []
    assert any("0 work units" in w for w in plan.warnings)


def test_spec_from_triggers_builds_valid_spec(tmp_path):
    _write(tmp_path, "a.yaml", _manifest("s.a"))
    _write(tmp_path, "b.yaml", _manifest("s.b", after=["s.a"]))
    spec, units, _ = spec_from_triggers(tmp_path)
    assert spec["independent_units"] == 2
    assert spec["units_detail"] == [{"id": "s.a"}, {"id": "s.b"}]
    assert spec["needs_write"] is True
    assert units[1]["after"] == ["s.a"]


# -- cli --------------------------------------------------------------------


def test_cli_plan_from_triggers_json(tmp_path, capsys):
    _write(tmp_path, "a.yaml", _manifest("cli.a"))
    _write(tmp_path, "b.yaml", _manifest("cli.b", after=["cli.a"]))
    rc = main(["plan", "--from-triggers", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["workers"] == 2
    assert out["collect"] is True
    assert [u["id"] for u in out["units"]] == ["cli.a", "cli.b"]


def test_cli_plan_from_triggers_explain(tmp_path, capsys):
    _write(tmp_path, "a.yaml", _manifest("cli.a"))
    _write(tmp_path, "b.yaml", _manifest("cli.b", after=["cli.a"]))
    assert main(["plan", "--from-triggers", str(tmp_path),
                 "--explain"]) == 0
    out = capsys.readouterr().out
    assert "trigger DAG order" in out
    assert "cli.a" in out and "cli.b" in out


def test_cli_plan_from_triggers_event_filter(tmp_path, capsys):
    _write(tmp_path, "a.yaml", _manifest("cli.sched", event="Scheduled"))
    _write(tmp_path, "b.yaml", _manifest("cli.end", event="SessionEnd"))
    rc = main(["plan", "--from-triggers", str(tmp_path),
               "--event", "SessionEnd"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [u["id"] for u in out["units"]] == ["cli.end"]
    assert out["workers"] == 0  # single unit stays inline


def test_cli_plan_from_triggers_errors(tmp_path, capsys):
    _write(tmp_path, "bad.yaml", "id: x\n\tbroken\n")
    assert main(["plan", "--from-triggers", str(tmp_path)]) == 2
    assert "error:" in capsys.readouterr().err

    cyc = tmp_path / "cyc"
    cyc.mkdir()
    _write(cyc, "cyc.yaml", _manifest("c.a", after=["c.a"]))
    assert main(["plan", "--from-triggers", str(cyc)]) == 2
    assert "cycle in DAG" in capsys.readouterr().err


def test_cli_event_without_from_triggers_is_rejected(capsys):
    assert main(["plan", '{"kind":"question"}', "--event", "X"]) == 2
    assert "--from-triggers" in capsys.readouterr().err


def test_cli_plan_from_triggers_never_emits_commands(tmp_path, capsys):
    _write(tmp_path, "a.yaml", _manifest("cli.a"))
    _write(tmp_path, "b.yaml", _manifest("cli.b", after=["cli.a"]))
    assert main(["plan", "--from-triggers", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "wscript" not in out and "scripts/x.py" not in out
