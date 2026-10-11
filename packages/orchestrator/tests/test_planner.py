"""Behavioral matrix for the fan-out planner — no workers are spawned."""

import json

import pytest
from devin_orchestrator.planner import MAX_WORKERS, plan_task, plan_task_json

# -- required matrix -------------------------------------------------------

def test_trivial_task_spawns_zero_workers():
    plan = plan_task({"kind": "fix", "estimated_scope": "trivial"})
    assert plan.workers == 0
    assert plan.mode == "none"


def test_question_spawns_zero_workers():
    plan = plan_task({"kind": "question"})
    assert plan.workers == 0


def test_single_unit_small_work_stays_inline():
    plan = plan_task(
        {"kind": "implementation", "independent_units": 1, "estimated_scope": "small"}
    )
    assert plan.workers == 0


def test_two_independent_units_get_two_workers():
    plan = plan_task(
        {
            "kind": "implementation",
            "independent_units": 2,
            "needs_write": True,
            "estimated_scope": "medium",
            "summary": "API + UI",
        }
    )
    assert plan.workers == 2
    assert plan.mode == "background"
    assert plan.profiles == ["subagent_general", "subagent_general"]
    assert plan.collect is True  # parent must gather results


def test_many_units_capped_at_max():
    plan = plan_task(
        {"kind": "implementation", "independent_units": 8, "estimated_scope": "large"}
    )
    assert plan.workers == MAX_WORKERS
    assert "capped" in plan.rationale


def test_large_refactor_bounded():
    plan = plan_task({"kind": "refactor", "independent_units": 1, "estimated_scope": "large"})
    assert 1 <= plan.workers <= MAX_WORKERS


def test_nested_fanout_blocked():
    plan = plan_task(
        {"kind": "implementation", "independent_units": 3},
        env={"DEVIN_INSIDE_SUBAGENT": "1"},
    )
    assert plan.workers == 0
    assert plan.nesting_blocked is True
    assert plan.warnings


def test_review_forces_readonly_profile():
    plan = plan_task(
        {"kind": "review", "independent_units": 2, "needs_write": True}
    )
    assert all(p == "subagent_explore" for p in plan.profiles)
    assert plan.warnings  # needs_write mismatch flagged


def test_env_cap_can_only_lower():
    plan = plan_task(
        {"kind": "implementation", "independent_units": 3},
        env={"DEVIN_MAX_WORKERS": "2"},
    )
    assert plan.workers == 2
    plan = plan_task(
        {"kind": "implementation", "independent_units": 3},
        env={"DEVIN_MAX_WORKERS": "99"},
    )
    assert plan.workers == MAX_WORKERS


# -- contract / safety -----------------------------------------------------

def test_plan_is_data_only():
    """The plan must carry no actionable paths, URLs or commands."""
    out = json.loads(
        plan_task_json(
            '{"kind":"implementation","independent_units":3,"summary":"x","needs_write":true}'
        )
    )
    blob = json.dumps(out)
    for forbidden in ("http://", "https://", "C:\\", "/home/", "git ", "gh "):
        assert forbidden not in blob
    assert out["limits"]["nesting"] == "forbidden"


def test_every_fanned_plan_requires_collection():
    for units in (2, 3, 5):
        plan = plan_task({"kind": "implementation", "independent_units": units})
        assert plan.collect is True, units


def test_invalid_specs_rejected():
    with pytest.raises(ValueError):
        plan_task("not a dict")
    with pytest.raises(ValueError):
        plan_task({"kind": "hack"})
    with pytest.raises(ValueError):
        plan_task({"kind": "implementation", "independent_units": -1})
    with pytest.raises(ValueError):
        plan_task({"kind": "implementation", "independent_units": True})
    with pytest.raises(ValueError):
        plan_task({"kind": "implementation", "estimated_scope": "huge"})
    with pytest.raises(ValueError):
        plan_task_json("{invalid json")


# -- OR-1/OR-2/OR-4: schemas, explain, file disjointness --------------------


def test_units_detail_collision_detected():
    plan = plan_task({
        "kind": "implementation",
        "independent_units": 2,
        "estimated_scope": "medium",
        "units_detail": [
            {"id": "api", "files": ["src/api/x.py", "src/api/"]},
            {"id": "ui", "files": ["src/api/x.py", "src/ui/"]},
        ],
    })
    assert plan.file_collisions
    assert plan.file_collisions[0]["units"] == ["api", "ui"]
    assert any("collision" in w for w in plan.warnings)


def test_units_detail_disjoint_clean():
    plan = plan_task({
        "kind": "implementation",
        "independent_units": 2,
        "estimated_scope": "medium",
        "units_detail": [
            {"id": "api", "files": ["src/api/"]},
            {"id": "ui", "files": ["src/ui/"]},
        ],
    })
    assert plan.file_collisions == []
    assert not any("collision" in w for w in plan.warnings)


def test_units_detail_dir_prefix_collision():
    plan = plan_task({
        "kind": "implementation",
        "independent_units": 2,
        "estimated_scope": "medium",
        "units_detail": [
            {"id": "a", "files": ["src/"]},
            {"id": "b", "files": ["src/deep/file.py"]},
        ],
    })
    assert plan.file_collisions == [{"units": ["a", "b"], "path": "src"}]


def test_units_detail_validation():
    import pytest
    with pytest.raises(ValueError):
        plan_task({"units_detail": [{"files": ["x"]}]})  # missing id
    with pytest.raises(ValueError):
        plan_task({"units_detail": [{"id": "a", "files": "x.py"}]})


def test_schemas_shipped_and_loadable():
    import json
    from importlib.resources import files
    spec = json.loads(
        files("devin_orchestrator").joinpath("spec.schema.json").read_text())
    plan = json.loads(
        files("devin_orchestrator").joinpath("plan.schema.json").read_text())
    assert "units_detail" in spec["properties"]
    assert "est_unit_minutes" in spec["properties"]
    assert "file_collisions" in plan["properties"]
    assert "cost_estimate" in plan["properties"]


# -- cost estimate -----------------------------------------------------------


def test_estimate_cost_fanout_scheduling():
    from devin_orchestrator.planner import estimate_cost
    spec = {"kind": "implementation", "independent_units": 4,
            "estimated_scope": "medium"}
    est = estimate_cost(spec, plan_task(spec))  # capped at 3 workers
    assert est["unit_count"] == 4
    assert est["sequential_minutes"] == 120.0
    assert est["fanout_minutes"] == 60.0  # lanes 60/30/30
    assert est["planned_workers"] == 3
    assert est["estimated_sessions"] == 4
    assert est["speedup"] == 2.0


def test_estimate_cost_top_level_default():
    from devin_orchestrator.planner import estimate_cost
    spec = {"kind": "implementation", "independent_units": 2,
            "est_unit_minutes": 10}
    est = estimate_cost(spec, plan_task(spec))
    assert [u["est_minutes"] for u in est["units"]] == [10.0, 10.0]
    assert est["fanout_minutes"] == 10.0


def test_estimate_cost_detail_outnumbers_declared_units():
    from devin_orchestrator.planner import estimate_cost
    spec = {"kind": "implementation",
            "units_detail": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    est = estimate_cost(spec, plan_task(spec))
    assert est["unit_count"] == 3  # detail wins over the default of 1
    assert [u["id"] for u in est["units"]] == ["a", "b", "c"]


def test_estimate_cost_minutes_validation():
    with pytest.raises(ValueError):
        plan_task({"est_unit_minutes": -5})
    with pytest.raises(ValueError):
        plan_task({"est_unit_minutes": 0})
    with pytest.raises(ValueError):
        plan_task({"units_detail": [{"id": "a", "est_minutes": "fast"}]})


def test_estimate_cost_single_unit_split():
    from devin_orchestrator.planner import estimate_cost
    # a large single-area task is decomposed across the workers — the
    # estimate must reflect the planned split, not a 1.0 speedup
    spec = {"kind": "refactor", "independent_units": 1,
            "estimated_scope": "large"}
    est = estimate_cost(spec, plan_task(spec))
    assert est["planned_workers"] == 3
    assert est["sequential_minutes"] == 60.0
    assert est["fanout_minutes"] == 20.0
    assert est["speedup"] == 3.0


def test_estimate_cost_rejects_non_finite_minutes():
    for bad in (float("nan"), float("inf"), 1e400):
        with pytest.raises(ValueError):
            plan_task({"est_unit_minutes": bad})
        with pytest.raises(ValueError):
            plan_task(
                {"units_detail": [{"id": "a", "est_minutes": bad}]})


def test_estimate_cost_tiny_units_keep_speedup():
    from devin_orchestrator.planner import estimate_cost
    # display rounding to 2 decimals must not collapse a real saving
    spec = {"kind": "implementation", "independent_units": 2,
            "est_unit_minutes": 0.001}
    est = estimate_cost(spec, plan_task(spec))
    assert est["speedup"] == 2.0
