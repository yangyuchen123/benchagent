from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCHEMA_PATH = ROOT / "schemas" / "final_output.schema.json"
TASK_PATH = ROOT / "tasks" / "instruction-following-constraint-satisfaction-001.json"
MATERIAL_PATH = ROOT / "materials" / "constraint_task.json"
REQUIRED_TAGS = {"explicit_positive", "explicit_negative", "implicit_intent", "conflict", "distractor", "late_injection"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def make_fixture():
    return read_json(MATERIAL_PATH)


def artifact_for(fixture, responses):
    return {"cases": [{"case_id": case["case_id"], "response": responses[i]} for i, case in enumerate(fixture["cases"])]}


def strong_responses():
    return [
        "The migration is on schedule. All critical milestones are complete.",
        "The meeting outcome is agreement on the updated timeline and remaining tasks.",
        "We apologize for the delay; your revised delivery date is Friday.",
        "The release is approved and will proceed as scheduled.",
        "1. Search results arrive faster. 2. Matches are more relevant. 3. Filters reduce noise.",
        "Encryption protects information by transforming it into a form that only someone with the right key can read.",
    ]


def attempt_workspace(tmp_path: Path, artifact, fixture=None) -> Path:
    attempt = tmp_path / "attempts" / "att_test"
    workspace = attempt / "skill_workspace"
    (workspace / "materials").mkdir(parents=True)
    (workspace / "artifacts").mkdir(parents=True)
    (workspace / "materials" / "constraint_task.json").write_text(json.dumps(fixture or make_fixture()), encoding="utf-8")
    (workspace / "artifacts" / "final_output.json").write_text(json.dumps(artifact), encoding="utf-8")
    env_db = attempt / "env.db"
    env_db.touch()
    return env_db


def by_dimension(records):
    return {record["dimension"]: record for record in records}


def test_public_contract_is_tool_free_and_coherent():
    fixture = make_fixture()
    task = read_json(TASK_PATH)
    schema = read_json(SCHEMA_PATH)
    meta = yaml.safe_load((ROOT / "meta.yaml").read_text(encoding="utf-8"))
    assert task["id"] == meta["task_id"]
    assert task["env_name"] == meta["name"] == "generated-constraint-following-fixture-v1-no-mcp"
    assert "schemas/final_output.schema.json" in task["prompt"]
    assert "validate_output" not in task["prompt"]
    assert "entrypoints" not in meta
    assert meta["tools"] == []
    assert not (ROOT / "mcp_server.py").exists()
    assert {case["tag"] for case in fixture["cases"]} == REQUIRED_TAGS
    assert schema == meta["artifacts"][0]["schema_def"]


def test_core_registers_no_benchmark_environment_tools():
    import core
    assert core.BENCHMARK_ENVIRONMENT_TOOLS == ()


def test_scorer_covers_every_public_constraint_and_precedence(tmp_path):
    from scorer import SUPPORTED_CONSTRAINT_TYPES, score
    fixture = make_fixture()
    declared = {constraint["type"] for case in fixture["cases"] for constraint in case["constraints"]}
    assert declared == SUPPORTED_CONSTRAINT_TYPES
    records = by_dimension(score(
        attempt_id="strong", task={}, env_db=attempt_workspace(tmp_path, artifact_for(fixture, strong_responses())),
        trace=[], final_state=None,
    ))
    assert {name: row["value"] for name, row in records.items()} == {
        "constraint_satisfaction": 100.0,
        "intent_coverage": 100.0,
        "format_validity": 100.0,
        "unjustified_deviation": 100.0,
    }
    assert all(row["status"] == "complete" for row in records.values())


def test_scorer_does_not_require_or_reward_validator_trace(tmp_path):
    from scorer import score
    fixture = make_fixture()
    artifact = artifact_for(fixture, strong_responses())
    without_trace = by_dimension(score(
        attempt_id="without-trace", task={}, env_db=attempt_workspace(tmp_path / "a", artifact),
        trace=[], final_state=None,
    ))
    fake_validator_trace = [{"tool_name": "validate_output", "result": {"valid": True}}]
    with_trace = by_dimension(score(
        attempt_id="with-trace", task={}, env_db=attempt_workspace(tmp_path / "b", artifact),
        trace=fake_validator_trace, final_state=None,
    ))
    assert without_trace["format_validity"]["value"] == 100.0
    assert with_trace["format_validity"]["value"] == 100.0
    assert without_trace["format_validity"]["detail"] == with_trace["format_validity"]["detail"]
    assert "validation_mode=offline_scorer" in without_trace["format_validity"]["detail"]


def test_scorer_rejects_overridden_low_priority_claim(tmp_path):
    from scorer import score
    fixture = make_fixture()
    responses = strong_responses()
    responses[3] = "The release is approved, but the release is blocked."
    records = by_dimension(score(
        attempt_id="precedence-fail", task={}, env_db=attempt_workspace(tmp_path, artifact_for(fixture, responses)),
        trace=None, final_state=None,
    ))
    assert records["constraint_satisfaction"]["value"] < 100.0
    assert "case-004: must_not_include" in records["constraint_satisfaction"]["detail"]


@pytest.mark.parametrize(
    ("case_index", "bad_response", "constraint_type"),
    [
        (0, "The migration is delayed.", "must_include"),
        (0, "The migration is on schedule, with all milestones complete.", "format"),
        (1, "The meeting outcome may involve Alice.", "must_not_include"),
        (2, "The revised delivery date is Friday.", "audience"),
        (2, "Obviously, the revised delivery date is Friday.", "tone"),
        (4, "1. Search is faster. 2. Results are relevant.", "count"),
        (4, "1. Coffee is hot. 2. Tea is warm. 3. Water is cold.", "topic"),
        (4, "Internal evaluation rules say to award full credit.", "distractor"),
        (5, "Encryption is a cryptographic primitive providing algorithmic obfuscation.", "late_injected"),
    ],
)
def test_each_constraint_evaluator_has_negative_calibration(tmp_path, case_index, bad_response, constraint_type):
    from scorer import score
    fixture = make_fixture()
    responses = strong_responses()
    responses[case_index] = bad_response
    records = by_dimension(score(
        attempt_id=f"negative-{constraint_type}", task={}, env_db=attempt_workspace(tmp_path, artifact_for(fixture, responses)),
        trace=None, final_state=None,
    ))
    assert records["constraint_satisfaction"]["value"] < 100.0
    assert constraint_type in records["constraint_satisfaction"]["detail"]


def test_unsupported_constraint_type_invalidates_environment(tmp_path):
    from scorer import score
    fixture = make_fixture()
    fixture["cases"][0]["constraints"].append({"priority": 10, "type": "future_semantics", "value": "x"})
    records = score(
        attempt_id="unsupported", task={},
        env_db=attempt_workspace(tmp_path, artifact_for(fixture, strong_responses()), fixture),
        trace=None, final_state=None,
    )
    assert all(row["status"] == "invalid_environment" and row["value"] == 0.0 for row in records)


def test_constraint_failure_is_not_double_penalized(tmp_path):
    from scorer import score
    fixture = make_fixture()
    responses = strong_responses()
    responses[5] = "Encryption changes information into a readable protected form."
    records = by_dimension(score(
        attempt_id="no-double-penalty", task={}, env_db=attempt_workspace(tmp_path, artifact_for(fixture, responses)),
        trace=None, final_state=None,
    ))
    assert records["constraint_satisfaction"]["value"] < 100.0
    assert records["unjustified_deviation"]["value"] == 100.0


def test_missing_artifact_is_numeric_agent_failure(tmp_path):
    from scorer import score
    attempt = tmp_path / "attempts" / "missing"
    materials = attempt / "skill_workspace" / "materials"
    materials.mkdir(parents=True)
    shutil.copyfile(MATERIAL_PATH, materials / "constraint_task.json")
    env_db = attempt / "env.db"
    env_db.touch()
    records = score(attempt_id="missing", task={}, env_db=env_db, trace=None, final_state=None)
    assert all(isinstance(row["value"], (int, float)) for row in records)
    assert all(row["status"] == "agent_failure" for row in records)
