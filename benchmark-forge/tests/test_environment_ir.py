from benchmark_forge import (
    ArtifactRequirement, ContentReference, EnvironmentContract, ExecutableTaskContract,
    ScoringContract, ScoringDimensionContract, ToolContract, WorkspaceContract,
)
from benchmark_forge.environment_ir import (
    IRComponentOutput, IRComponentFile, IRValidationError,
    link_component_outputs, lower_contract_to_ir,
)


def _contract():
    return ExecutableTaskContract(
        task_id="coordination-task",
        instruction="Use tools to produce a final report.",
        environment=EnvironmentContract(
            environment_id="generated-coordination-v1",
            tools=[ToolContract(name="fetch_data", interface="mcp", entrypoint={
                "input_schema": {"type": "object", "required": ["source"]},
                "output_schema": {"type": "object", "required": ["rows"]},
            })],
            entrypoints={"mcp": {"transport": "stdio"}},
            workspace=WorkspaceContract(writable_paths=["artifacts"]),
            implementation=ContentReference(type="generated", ref="generated-coordination-v1"),
        ),
        artifacts=[ArtifactRequirement(
            path="artifacts/final_report.json", description="Final report", media_type="application/json",
        )],
        scoring=ScoringContract(dimensions=[ScoringDimensionContract(
            name="completion", weight=100, description="Produces the final report", evidence_sources=["artifact", "tool_trace"],
        )], pass_threshold=60),
        observation_requirements=["capture tool trace"],
    )


def test_lower_contract_creates_canonical_bindings():
    ir = lower_contract_to_ir(_contract()).freeze()
    assert ir.frozen is True
    assert ir.tools[0].tool_id == "fetch_data"
    assert ir.artifacts[0].artifact_id == "final_report"
    assert ir.task_binding.artifact_refs == ["final_report"]
    assert {c.criterion_id for c in ir.rubric.criteria} == {"completion"}
    assert {e.evidence_id for e in ir.evidence} == {"evidence_artifact", "evidence_tool_trace"}


def test_linker_rejects_unfrozen_ir_and_path_collision():
    ir = lower_contract_to_ir(_contract())
    outputs = [
        IRComponentOutput(component_id="manifest", files=[
            IRComponentFile(path="meta.yaml", content="name: generated-coordination-v1"),
            IRComponentFile(path="tasks/task.json", content="{}"),
        ]),
        IRComponentOutput(component_id="runtime", files=[IRComponentFile(path="core.py", content="")]),
        IRComponentOutput(component_id="scorer", files=[IRComponentFile(path="scorer.py", content="def score(**kwargs): return []")]),
        IRComponentOutput(component_id="tests", files=[IRComponentFile(path="tests/test_contract.py", content="")]),
    ]
    try:
        link_component_outputs(ir, outputs)
    except IRValidationError as exc:
        assert "frozen" in str(exc)
    else:
        raise AssertionError("unfrozen IR must not link")

    frozen = ir.freeze()
    outputs[1].files.append(IRComponentFile(path="meta.yaml", content="collision"))
    try:
        link_component_outputs(frozen, outputs)
    except IRValidationError as exc:
        assert "collision" in str(exc)
    else:
        raise AssertionError("path collision must be rejected")


def test_linker_requires_all_components_and_returns_scaffold():
    ir = lower_contract_to_ir(_contract()).freeze()
    outputs = [
        IRComponentOutput(component_id="manifest", files=[
            IRComponentFile(path="meta.yaml", content="name: generated-coordination-v1"),
            IRComponentFile(path="tasks/task.json", content="{}"),
        ]),
        IRComponentOutput(component_id="runtime", files=[IRComponentFile(path="core.py", content="")]),
        IRComponentOutput(component_id="scorer", files=[IRComponentFile(path="scorer.py", content="def score(**kwargs): return []")]),
        IRComponentOutput(component_id="tests", files=[IRComponentFile(path="tests/test_contract.py", content="")]),
    ]
    bundle = link_component_outputs(ir, outputs)
    assert bundle.environment_id == "generated-coordination-v1"
    assert {file.path for file in bundle.files} >= {"meta.yaml", "core.py", "scorer.py", "tasks/task.json"}


def test_supported_coordination_extension_lowers_to_typed_graph():
    from benchmark_forge import AgentCapabilityRequirement, CoordinationContract, DelegatedSubtaskContract
    from benchmark_forge.environment_ir import lower_contract_to_ir

    contract = _contract().model_copy(update={
        "agent_capabilities": [AgentCapabilityRequirement(name="subagent_spawn")],
        "coordination": CoordinationContract(subtasks=[DelegatedSubtaskContract(
            subtask_id="child", objective="collect data", output_contract={"type": "object"},
        )]),
    })
    ir = lower_contract_to_ir(contract)
    assert ir.ir_version == "1.1"
    assert "coordination_graph" in ir.required_features
    assert ir.coordination is not None
    assert ir.coordination.nodes[0].node_id == "child"


def test_contract_expressiveness_gap_is_not_a_rewriteable_hole():
    from benchmark_forge.environment_ir import IRExpressivenessError, analyze_contract_expressiveness

    contract = _contract().model_copy(update={
        "constraints": {"fault_injection": {"mode": "network_partition"}},
    })
    try:
        analyze_contract_expressiveness(contract)
    except IRExpressivenessError as exc:
        assert "fault_model" in exc.missing_features
        assert "constraints.fault_injection" in exc.affected_constructs
    else:
        raise AssertionError("fault injection must require an IR extension")


def test_ir_rejects_unknown_required_feature_without_arbitrary_extensions():
    from benchmark_forge.environment_ir import IRExpressivenessError, normalize_ir_draft
    draft = lower_contract_to_ir(_contract()).model_copy(update={"required_features": ["made_up_feature"]})
    try:
        normalize_ir_draft(draft)
    except IRExpressivenessError as exc:
        assert "made_up_feature" in str(exc)
    else:
        raise AssertionError("unknown features must be rejected")


def test_freeze_attaches_stable_checksum_and_linker_records_it():
    ir = lower_contract_to_ir(_contract()).freeze()
    assert ir.ir_checksum == ir.semantic_checksum()
    assert ir.frozen_at is not None
    outputs = [
        IRComponentOutput(component_id="manifest", files=[
            IRComponentFile(path="meta.yaml", content="name: generated-coordination-v1"),
            IRComponentFile(path="tasks/task.json", content="{}"),
        ]),
        IRComponentOutput(component_id="runtime", files=[IRComponentFile(path="core.py", content="")]),
        IRComponentOutput(component_id="scorer", files=[IRComponentFile(path="scorer.py", content="def score(**kwargs): return []")]),
        IRComponentOutput(component_id="tests", files=[IRComponentFile(path="tests/test_contract.py", content="")]),
    ]
    bundle = link_component_outputs(ir, outputs)
    assert any(ir.ir_checksum in note for note in bundle.implementation_notes)


def test_linker_does_not_allow_prefix_collision_or_unknown_component():
    ir = lower_contract_to_ir(_contract()).freeze()
    outputs = [
        IRComponentOutput(component_id="manifest", files=[IRComponentFile(path="meta.yaml", content="")]),
        IRComponentOutput(component_id="runtime", files=[IRComponentFile(path="core.pyx", content="")]),
        IRComponentOutput(component_id="scorer", files=[IRComponentFile(path="scorer.py", content="")]),
        IRComponentOutput(component_id="tests", files=[IRComponentFile(path="tests/test.py", content="")]),
    ]
    try:
        link_component_outputs(ir, outputs)
    except IRValidationError as exc:
        assert "does not own path" in str(exc)
    else:
        raise AssertionError("core.pyx must not be treated as runtime-owned core.py")

    outputs[1].files[0] = IRComponentFile(path="core.py", content="")
    outputs.append(IRComponentOutput(component_id="manifest", files=[]))
    try:
        link_component_outputs(ir, outputs)
    except IRValidationError as exc:
        assert "duplicate component output" in str(exc)
    else:
        raise AssertionError("duplicate component output must be rejected")


def test_run_telemetry_is_secret_free_and_append_only(tmp_path):
    import json
    from benchmark_forge.pydantic_ai_adapter import RunTelemetry

    path = tmp_path / "telemetry.jsonl"
    telemetry = RunTelemetry(path, run_id="formal-test")
    telemetry.record(label="ir_compiler.attempt_1", output_type=dict,
                     status="failed", duration_seconds=1.234,
                     error="ValidationError: private prompt should not be stored")
    telemetry.record(label="component.scorer.generate", output_type=dict,
                     status="completed", duration_seconds=2.5)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["status"] for row in rows] == ["failed", "completed"]
    assert rows[0]["run_id"] == "formal-test"
    assert "private prompt" in rows[0]["error"]
    assert "prompt" not in rows[0]
    assert rows[1]["duration_seconds"] == 2.5


def test_evidence_package_uses_canonical_ir_ids_and_marks_missing_evidence():
    from benchmark_forge.scoring import normalize_evidence
    ir = lower_contract_to_ir(_contract()).freeze()
    package = normalize_evidence(
        ir, attempt_id="attempt-1",
        artifacts={"final_report": {"summary": "ok"}},
        tool_events=[{"tool_id": "fetch_data", "status": "completed"}],
    )
    assert package.ir_checksum == ir.ir_checksum
    assert {record.evidence_id for record in package.artifacts} == {"artifact:final_report"}
    assert package.artifacts[0].observed is True
    assert any(check.status == "not_observed" for check in package.deterministic_checks)


def test_llm_evaluator_rejects_package_from_different_ir():
    import pytest
    from benchmark_forge.scoring import LLMRubricEvaluator, normalize_evidence
    ir = lower_contract_to_ir(_contract()).freeze()
    package = normalize_evidence(ir, attempt_id="attempt-1")
    changed = ir.model_copy(update={"task_id": "other-task"})
    with pytest.raises(ValueError, match="checksum"):
        LLMRubricEvaluator(model=object()).evaluate(changed, package)


def test_evidence_bindings_are_explicit_and_canonical():
    from benchmark_forge.scoring import normalize_evidence
    ir = lower_contract_to_ir(_contract()).freeze()
    package = normalize_evidence(
        ir, attempt_id="attempt-bindings",
        artifacts={"final_report": {"summary": "ok"}},
        tool_events=[{"tool_id": "fetch_data", "event_type": "tool_call.fetch_data", "status": "completed"}],
    )
    assert package.evidence_bindings["evidence_artifact"] == ["artifact:final_report"]
    assert package.evidence_bindings["evidence_tool_trace"] == ["tool_event:0"]
    assert package.allowed_refs(["evidence_tool_trace"]) == {"tool_event:0"}


def test_rubric_evaluation_validation_recomputes_weighted_score_and_verdict():
    from benchmark_forge.scoring import (
        RubricCriterionEvaluation, RubricEvaluation, normalize_evidence,
        validate_rubric_evaluation,
    )
    ir = lower_contract_to_ir(_contract()).freeze()
    package = normalize_evidence(
        ir, attempt_id="attempt-score",
        artifacts={"final_report": {"summary": "ok"}},
        tool_events=[{"tool_id": "fetch_data", "event_type": "tool_call.fetch_data"}],
        verifier_evidence=[{"ok": True}],
    )
    result = RubricEvaluation(
        attempt_id="attempt-score", ir_checksum=ir.ir_checksum, rubric_id=ir.rubric.rubric_id,
        evaluator_model="test", criteria=[RubricCriterionEvaluation(
            criterion_id="completion", score=80, confidence=1, verdict="pass",
            evidence_refs=["artifact:final_report"], reason="observed",
        )], overall_score=80, overall_verdict="pass",
    )
    assert validate_rubric_evaluation(ir, package, result) == result

    import pytest
    with pytest.raises(ValueError, match="overall_score mismatch"):
        validate_rubric_evaluation(ir, package, result.model_copy(update={"overall_score": 81}))
    with pytest.raises(ValueError, match="outside criterion binding"):
        validate_rubric_evaluation(ir, package, result.model_copy(update={
            "criteria": [result.criteria[0].model_copy(update={"evidence_refs": ["verifier:0"]})]
        }))




def test_rubric_integrity_review_is_generation_time_and_checks_drift():
    from benchmark_forge.rubric_review import RubricCriterionReview, RubricIntegrityReview, validate_rubric_integrity_review
    ir = lower_contract_to_ir(_contract()).freeze()
    review = RubricIntegrityReview(
        verdict="pass", target_alignment="aligned", summary="aligned", confidence=1,
        criterion_reviews=[RubricCriterionReview(
            criterion_id="completion", target_covered=True, scope="appropriate",
            direction="correct", evidence_plausible=True,
        )],
    )
    assert validate_rubric_integrity_review(ir, review) == review
    import pytest
    with pytest.raises(ValueError, match="unsafe criterion"):
        validate_rubric_integrity_review(ir, review.model_copy(update={
            "criterion_reviews": [review.criterion_reviews[0].model_copy(update={"scope": "too_broad"})]
        }))


def test_pydantic_runner_enforces_total_wall_clock_timeout():
    import time
    import pytest
    from types import SimpleNamespace
    from benchmark_forge.pydantic_ai_adapter import PydanticAIRunner

    class SlowAgent:
        def run_sync(self, *args, **kwargs):
            time.sleep(0.2)
            return SimpleNamespace(output={})

    runner = object.__new__(PydanticAIRunner)
    runner.agent = SlowAgent()
    runner.timeout = 0.03
    runner.retries = 0
    runner.label = "slow-test"
    runner.output_type = dict
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="total wall-clock"):
        runner.run_sync("x")
    assert time.monotonic() - started < 0.15


def test_bounded_rubric_revision_preserves_scoring_policy():
    import pytest
    from benchmark_forge.rubric_review import (
        RubricCriterionReview, RubricIntegrityReview, validate_revised_rubric,
    )
    ir = lower_contract_to_ir(_contract()).freeze()
    review = RubricIntegrityReview(
        verdict="revise", target_alignment="aligned", summary="clarify evidence",
        criterion_reviews=[RubricCriterionReview(
            criterion_id="completion", target_covered=True, scope="too_narrow",
            direction="correct", evidence_plausible=True,
        )], repair_instructions=["clarify runtime evidence"],
    )
    revised = ir.rubric.model_copy(deep=True)
    revised.criteria[0].description = "Complete the public task using observable evidence."
    assert validate_revised_rubric(ir, revised, review) == revised
    changed_weight = revised.model_copy(deep=True)
    changed_weight.criteria[0].weight = revised.criteria[0].weight + 1
    with pytest.raises(ValueError, match="scoring policy"):
        validate_revised_rubric(ir, changed_weight, review)


def test_tool_ownership_survives_contract_lowering():
    contract = _contract()
    contract.environment.tools[0].ownership = "agent_runtime"
    ir = lower_contract_to_ir(contract)
    assert ir.tools[0].ownership == "agent_runtime"


def test_evaluation_system_tool_is_not_bound_to_public_task():
    contract = _contract()
    contract.environment.tools.append(ToolContract(
        name="hidden_verify", ownership="evaluation_system", interface="python"
    ))
    ir = lower_contract_to_ir(contract)
    assert "hidden_verify" in {tool.tool_id for tool in ir.tools}
    assert "hidden_verify" not in ir.task_binding.tool_refs
