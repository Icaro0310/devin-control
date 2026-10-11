"""CLI surface tests."""

import json

from devin_orchestrator.cli import main


def test_plan_command_prints_json(capsys):
    rc = main(["plan", '{"kind":"implementation","independent_units":2}'])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["workers"] == 2
    assert out["collect"] is True


def test_plan_from_file(tmp_path, capsys):
    spec = tmp_path / "spec.json"
    spec.write_text('{"kind":"refactor","estimated_scope":"large","independent_units":5}')
    rc = main(["plan", "--spec-file", str(spec)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["workers"] == 3


def test_bad_spec_returns_2(capsys):
    assert main(["plan", "{bad"]) == 2
    assert "error:" in capsys.readouterr().err


def test_plan_explain_and_schema(capsys):
    from devin_orchestrator.cli import main
    assert main(["plan", '{"kind":"question"}', "--explain"]) == 0
    out = capsys.readouterr().out
    assert "0 worker(s)" in out and "inline" in out
    assert main(["schema", "spec"]) == 0
    import json as j
    assert "task spec" in j.loads(capsys.readouterr().out)["title"]


def test_record_and_history(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert main(["record", '{"workers":[1,2],"mode":"parallel"}',
                 "--outcome", "success", "--registry", str(reg)]) == 0
    assert main(["record", '{"workers":[1]}',
                 "--outcome", "failed", "--registry", str(reg)]) == 0
    capsys.readouterr()
    assert main(["history", "--registry", str(reg), "--json"]) == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 2
    assert out["by_outcome"] == {"success": 1, "failed": 1}
    assert "nothing leaves the machine" in out["note"]


def test_record_bad_outcome(tmp_path, capsys):
    import pytest
    with pytest.raises(SystemExit):  # argparse rejects invalid choices
        main(["record", "{}", "--outcome", "bogus",
              "--registry", str(tmp_path / "p.jsonl")])


def test_history_empty(tmp_path, capsys):
    assert main(["history", "--registry", str(tmp_path / "nope.jsonl")]) == 0
    assert "none" in capsys.readouterr().out


# -- plan --cost-estimate ---------------------------------------------------


def test_plan_cost_estimate_json(capsys):
    rc = main(["plan",
               ('{"kind":"implementation","independent_units":3,'
                '"estimated_scope":"medium"}'),
               "--cost-estimate"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    est = out["cost_estimate"]
    assert out["workers"] == 3  # plan fields still present
    assert est["unit"] == "minutes"
    assert est["unit_count"] == 3
    assert len(est["units"]) == 3
    assert est["planned_workers"] == 3
    assert est["estimated_sessions"] == 4  # 1 coordinator + 3 workers
    assert est["sequential_minutes"] == 90.0
    assert est["fanout_minutes"] == 30.0
    assert est["speedup"] == 3.0
    assert "not billing" in est["note"]
    assert est["assumptions"]


def test_plan_cost_estimate_inline_stays_sequential(capsys):
    rc = main(["plan", '{"kind":"question"}', "--cost-estimate"])
    assert rc == 0
    est = json.loads(capsys.readouterr().out)["cost_estimate"]
    assert est["planned_workers"] == 0
    assert est["estimated_sessions"] == 1
    assert est["fanout_minutes"] == est["sequential_minutes"]


def test_plan_cost_estimate_unit_overrides(capsys):
    spec = ('{"kind":"implementation","independent_units":2,'
            '"units_detail":[{"id":"api","est_minutes":10},'
            '{"id":"db","est_minutes":50}]}')
    assert main(["plan", spec, "--cost-estimate"]) == 0
    est = json.loads(capsys.readouterr().out)["cost_estimate"]
    assert [u["est_minutes"] for u in est["units"]] == [10.0, 50.0]
    assert est["sequential_minutes"] == 60.0
    assert est["fanout_minutes"] == 50.0  # critical path = longest lane


def test_plan_cost_estimate_explain(capsys):
    assert main(["plan",
                 ('{"kind":"implementation","independent_units":2,'
                  '"est_unit_minutes":10}'),
                 "--explain", "--cost-estimate"]) == 0
    out = capsys.readouterr().out
    assert "Cost estimate" in out
    assert "sequential" in out
    assert "assumption:" in out


def test_plan_cost_estimate_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["plan", '{"kind":"implementation","independent_units":2}',
                 "--cost-estimate"]) == 0
    capsys.readouterr()
    assert list(tmp_path.iterdir()) == []  # dry-run: no files touched
