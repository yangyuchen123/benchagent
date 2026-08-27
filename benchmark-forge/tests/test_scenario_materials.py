from __future__ import annotations

import json
from pathlib import Path

from benchmark_forge import (
    ArtifactRequirement, Benchmark, BenchmarkItem, ContentReference,
    EnvironmentCandidateRegistry, EnvironmentContract, ExecutableTaskContract,
    MaterialContract, ScenarioContract, ScoringContract,
    ScoringDimensionContract, ToolContract, UserGoal, WorkspaceContract,
    lower_contract_to_ir, stage_generated_candidates, validate_contract_realizability,
)
from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, normalize_octagon_scaffold, validate_scaffold


def _data_item(*, with_material: bool = True) -> BenchmarkItem:
    materials = [MaterialContract(
        material_id="change_records",
        source=ContentReference(type="generated", ref="change-record-generator-v1"),
        target="materials/records.json",
        minimum_items=3,
        collection_key="records",
        schema_ref="schemas/change-record.schema.json",
    )] if with_material else []
    scenario = ScenarioContract(
        data_dependent=True,
        material_refs=["change_records"] if with_material else [],
        minimum_items=3,
        required_case_tags=["eligible", "excluded", "injection"],
        case_tag_field="_case_tags",
    ) if with_material else None
    contract = ExecutableTaskContract(
        task_id="scenario-task",
        instruction="读取记录资料，调用 list_records 和 get_record，筛选后生成报告。",
        environment=EnvironmentContract(
            environment_id="scenario-environment-v1",
            implementation=ContentReference(type="generated", ref="scenario-fixture-v1"),
            tools=[
                ToolContract(name="list_records", interface="mcp"),
                ToolContract(name="get_record", interface="mcp"),
            ],
            materials=materials,
            scenario=scenario,
            entrypoints={"mcp": {"transport": "stdio"}},
            workspace=WorkspaceContract(writable_paths=["artifacts"]),
            maturity="generated_contract",
        ),
        artifacts=[ArtifactRequirement(path="artifacts/report.json", description="report")],
        scoring=ScoringContract(dimensions=[ScoringDimensionContract(
            name="completion", weight=100, description="complete", evidence_sources=["artifact"],
        )], pass_threshold=60),
    )
    return BenchmarkItem(
        item_id="scenario-item", dimension_id="instruction_following",
        source_mode="generated_environment", source_id="test",
        item_kind="executable_task", answer_type="artifact", executable_task=contract,
    )


def _bundle(item: BenchmarkItem, *, include_material: bool, scorer_invalid_status: bool = True):
    files = [
        ScaffoldFile(path="meta.yaml", content="name: scenario-environment-v1\n"),
        ScaffoldFile(path="README.md", content="scenario"),
        ScaffoldFile(path="tasks/scenario-task.json", content="{}"),
        ScaffoldFile(path="core.py", content="def load():\n    return True\n"),
        ScaffoldFile(path="mcp_server.py", content="def main():\n    return None\n"),
        ScaffoldFile(
            path="scorer.py",
            content=(
                "INVALID_STATUS = 'invalid_environment'\n" if scorer_invalid_status else ""
            ) + "def score(*, attempt_id, task, env_db=None, trace=None, final_state=None, **kwargs):\n    return []\n",
        ),
        ScaffoldFile(path="tests/test_contract.py", content="def test_contract():\n    assert True\n"),
    ]
    if include_material:
        records = {
            "records": [
                {"record_id": "1", "_case_tags": ["eligible"]},
                {"record_id": "2", "_case_tags": ["excluded"]},
                {"record_id": "3", "_case_tags": ["injection"]},
            ]
        }
        files.extend([
            ScaffoldFile(path="materials/records.json", content=json.dumps(records)),
            ScaffoldFile(path="schemas/change-record.schema.json", content="{}"),
        ])
    ir = lower_contract_to_ir(item.executable_task).freeze()
    return normalize_octagon_scaffold(
        EnvironmentScaffoldBundle(environment_id="scenario-environment-v1", files=files), item, ir,
    )


def test_legacy_generated_data_task_without_binding_is_blocked_before_ir(tmp_path):
    item = _data_item(with_material=False)
    errors = validate_contract_realizability(item)
    assert any("no MaterialContract" in error for error in errors)

    benchmark = Benchmark(
        benchmark_id="b", user_goal=UserGoal(goal_id="g", description="x", target_size=1),
        items=[item],
    )
    candidate = stage_generated_candidates(
        benchmark, EnvironmentCandidateRegistry(tmp_path / "candidates"),
    )[0]
    assert candidate.status.value == "scenario_incomplete"
    assert candidate.environment_ir is None
    assert next(check for check in candidate.checks if check.check_id == "scenario_contract").status == "failed"


def test_material_and_scenario_lower_to_typed_ir_extension():
    item = _data_item()
    assert validate_contract_realizability(item) == []
    ir = lower_contract_to_ir(item.executable_task).freeze()
    assert ir.ir_version == "1.2"
    assert {"material_registry", "scenario_model"} <= set(ir.required_features)
    assert ir.materials[0].material_id == "change_records"
    assert ir.task_binding.material_refs == ["change_records"]
    assert ir.scenario is not None
    assert ir.scenario.minimum_items == 3
    manifest = next(component for component in ir.components if component.component_id == "manifest")
    assert {"materials/", "schemas/"} <= set(manifest.owned_paths)


def test_scenario_completeness_rejects_missing_production_material():
    item = _data_item()
    validation = validate_scaffold(_bundle(item, include_material=False), item)
    assert validation.valid is False
    assert any("required generated material missing" in error for error in validation.errors)
    assert any("scenario has 0 observable items" in error for error in validation.errors)


def test_scenario_completeness_accepts_typed_material_and_required_tags():
    item = _data_item()
    validation = validate_scaffold(_bundle(item, include_material=True), item)
    assert validation.valid is True, validation.errors


def test_data_dependent_scorer_requires_invalid_environment_protocol():
    item = _data_item()
    validation = validate_scaffold(
        _bundle(item, include_material=True, scorer_invalid_status=False), item,
    )
    assert validation.valid is False
    assert any("invalid_environment" in error for error in validation.errors)


def test_large_material_body_is_not_copied_into_downstream_agent_prompt():
    from benchmark_forge.component_agents import _dependency_view
    from benchmark_forge.environment_ir import IRComponentFile, IRComponentOutput

    body = json.dumps({"records": [{"id": index, "text": "x" * 100} for index in range(50)]})
    output = IRComponentOutput(component_id="manifest", files=[
        IRComponentFile(path="materials/records.json", content=body),
        IRComponentFile(path="meta.yaml", content="name: x"),
    ])
    viewed = _dependency_view([output])[0]
    material = next(file for file in viewed.files if file.path.startswith("materials/"))
    assert "material body omitted" in material.content
    assert "sha256=" in material.content
    assert next(file for file in viewed.files if file.path == "meta.yaml").content == "name: x"


def test_later_failed_checks_do_not_mask_scenario_incomplete_status(tmp_path):
    from benchmark_forge import CandidateCheck

    item = _data_item(with_material=False)
    benchmark = Benchmark(
        benchmark_id="b2", user_goal=UserGoal(goal_id="g2", description="x", target_size=1),
        items=[item],
    )
    registry = EnvironmentCandidateRegistry(tmp_path / "candidates")
    candidate = stage_generated_candidates(benchmark, registry)[0]
    assert candidate.status.value == "scenario_incomplete"
    candidate = registry.record_check(candidate.candidate_id, CandidateCheck(
        check_id="contract_schema", stage="static", status="failed", summary="secondary failure",
    ))
    assert candidate.status.value == "scenario_incomplete"


def test_misplaced_typed_material_bindings_are_normalized_without_invention():
    from benchmark_forge import normalize_contract_bindings

    item = _data_item(with_material=False)
    contract = item.executable_task.model_copy(update={
        "context": {
            "material_contracts": [{
                "material_id": "records",
                "source": {"type": "generated", "ref": "generator-v1"},
                "target": "materials/records.json",
                "minimum_items": 2,
            }],
            "scenario": {
                "data_dependent": True,
                "material_refs": ["records"],
                "minimum_items": 2,
            },
            "unrelated": "preserved",
        },
    })
    normalized = normalize_contract_bindings(contract)
    assert normalized.environment.materials[0].material_id == "records"
    assert normalized.environment.scenario is not None
    assert normalized.environment.scenario.material_refs == ["records"]
    assert normalized.context == {"unrelated": "preserved"}
    assert validate_contract_realizability(item.model_copy(update={"executable_task": normalized})) == []


def test_invalid_misplaced_binding_is_not_silently_invented_or_removed():
    from benchmark_forge import normalize_contract_bindings

    item = _data_item(with_material=False)
    contract = item.executable_task.model_copy(update={
        "context": {"material_contracts": [{"not": "a material"}]},
    })
    normalized = normalize_contract_bindings(contract)
    assert normalized.environment.materials == []
    assert "material_contracts" in normalized.context
