"""Per-run ``metrics.json`` emitted by ``record``.

Every recorded run writes ``metrics/<run_id>.json`` beside ``plans.jsonl``
— always (no opt-out), local observability only, so a metrics write
failure must never break the record itself.
"""

import json

import pytest
from devin_orchestrator.cli import main


def _record(reg, outcome="success", *extra,
            plan='{"workers":["a","b"],"mode":"background"}'):
    return main(["record", plan, "--outcome", outcome,
                 "--registry", str(reg), *extra])


def _metrics(tmp_path, run_id):
    return json.loads(
        (tmp_path / "metrics" / f"{run_id}.json").read_text())


def test_record_writes_metrics_file(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(
        reg, "success",
        "--run-id", "r-1", "--session-id", "sess-9", "--duration", "120",
        "--units", '[{"id":"api","status":"success","duration_s":70},'
                   '{"id":"ui","status":"failed","duration_s":50}]') == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_id"] == "r-1"
    assert out["metrics_path"].endswith("r-1.json")
    doc = _metrics(tmp_path, "r-1")
    assert doc["version"] == 1
    assert doc["run_id"] == "r-1"
    assert doc["session_id"] == "sess-9"
    assert doc["workers"] == 2
    assert doc["mode"] == "background"
    assert doc["outcome"] == "success"
    assert doc["duration_s"] == 120.0
    assert doc["ended_ts"] == doc["recorded_ts"]
    assert doc["started_ts"] == pytest.approx(doc["ended_ts"] - 120)
    assert doc["units"] == [
        {"id": "api", "status": "success", "duration_s": 70},
        {"id": "ui", "status": "failed", "duration_s": 50},
    ]
    assert doc["outcome_counts"] == {"success": 1, "failed": 1}
    assert "nothing leaves the machine" in doc["note"]


def test_metrics_run_id_generated_and_linked(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(reg) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_id"].startswith("run-")
    line = json.loads(reg.read_text().strip())
    assert line["run_id"] == out["run_id"]
    doc = _metrics(tmp_path, out["run_id"])
    assert doc["run_id"] == out["run_id"]
    assert doc["session_id"] is None
    assert doc["units"] == []
    assert doc["outcome_counts"] == {"success": 1}
    assert doc["duration_s"] is None
    assert doc["started_ts"] is None


def test_metrics_derives_duration_from_started(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(reg, "success", "--started", "1") == 0
    out = json.loads(capsys.readouterr().out)
    doc = _metrics(tmp_path, out["run_id"])
    assert doc["started_ts"] == 1
    assert doc["duration_s"] == pytest.approx(doc["ended_ts"] - 1)


def test_metrics_bad_units_fails_before_writing(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(reg, "success", "--units", '{"a":1}') == 2
    assert "error:" in capsys.readouterr().err
    assert _record(reg, "success", "--units", '[{"status":"x"}]') == 2
    assert _record(reg, "success", "--units",
                   '[{"id":"a","duration_s":-3}]') == 2
    capsys.readouterr()
    assert not reg.exists()  # validation aborted the record entirely
    assert not (tmp_path / "metrics").exists()


def test_metrics_negative_duration_rejected(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(reg, "success", "--duration", "-1") == 2
    assert not reg.exists()


def test_metrics_run_id_is_filename_safe(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(reg, "success", "--run-id", "../escape/../me") == 0
    out = json.loads(capsys.readouterr().out)
    path = tmp_path / "metrics" / f"{out['run_id']}.json"
    assert path.is_file()
    assert path.parent == tmp_path / "metrics"
    assert ".." not in path.name and "/" not in out["run_id"]


def test_metrics_write_failure_does_not_break_record(tmp_path, capsys):
    (tmp_path / "metrics").write_text("not a dir")
    reg = tmp_path / "plans.jsonl"
    assert _record(reg) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert "metrics_path" not in out
    assert "warning:" in captured.err
    assert len(reg.read_text().splitlines()) == 1  # record still landed


def test_metrics_counts_custom_unit_statuses(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(
        reg, "partial",
        "--units", '[{"id":"a","status":"success"},'
                   '{"id":"b","status":"timeout"}]') == 0
    out = json.loads(capsys.readouterr().out)
    doc = _metrics(tmp_path, out["run_id"])
    assert doc["outcome"] == "partial"
    assert doc["outcome_counts"] == {"success": 1, "timeout": 1}


def test_metrics_colliding_run_ids_keep_separate_docs(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(reg, "success", "--run-id", "a/b") == 0
    assert _record(reg, "failed", "--run-id", "a-b") == 0
    capsys.readouterr()
    # distinct raw ids slug to the same name — the recorded id is
    # uniquified so every run_id resolves to its own metrics file
    lines = [json.loads(l) for l in reg.read_text().splitlines()]
    assert [l["run_id"] for l in lines] == ["a-b", "a-b-2"]
    first = _metrics(tmp_path, "a-b")
    second = _metrics(tmp_path, "a-b-2")
    assert first["outcome"] == "success" and first["run_id"] == "a-b"
    assert second["outcome"] == "failed" and second["run_id"] == "a-b-2"


def test_metrics_repeated_run_id_keeps_both_docs(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(reg, "success", "--run-id", "r-1") == 0
    assert _record(reg, "failed", "--run-id", "r-1") == 0
    capsys.readouterr()
    lines = [json.loads(l) for l in reg.read_text().splitlines()]
    assert [l["run_id"] for l in lines] == ["r-1", "r-1-2"]
    assert _metrics(tmp_path, "r-1")["outcome"] == "success"
    assert _metrics(tmp_path, "r-1-2")["outcome"] == "failed"


def test_metrics_non_finite_values_rejected(tmp_path, capsys):
    reg = tmp_path / "plans.jsonl"
    assert _record(reg, "success", "--duration", "NaN") == 2
    assert _record(reg, "success", "--duration", "inf") == 2
    assert _record(reg, "success", "--started", "inf") == 2
    assert _record(reg, "success",
                   "--units", '[{"id":"a","duration_s":NaN}]') == 2
    capsys.readouterr()
    assert not reg.exists()
    assert not (tmp_path / "metrics").exists()
