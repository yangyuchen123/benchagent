import pytest

from benchmark_forge import Benchmark, UserGoal
from benchmark_forge.staging import (
    AgentEvalReportRef, CandidateCheck, EnvironmentCandidateRegistry,
    HumanApproval, PilotTrial, stage_generated_candidates,
)
from benchmark_forge.domain import (
    ArtifactRequirement, BenchmarkItem, ContentReference, EnvironmentContract,
    ExecutableTaskContract, ScoringContract, ScoringDimensionContract,
    SourceMode, SourceRef, ToolContract,
)


def generated_item():
    return BenchmarkItem(
        item_id="i", dimension_id="d", source_mode=SourceMode.GENERATED_ENVIRONMENT,
        source_id="rag-environment-blueprint", item_kind="executable_task",
        answer_type="agent_trajectory",
        executable_task=ExecutableTaskContract(
            task_id="t", instruction="complete an open tool task",
            environment=EnvironmentContract(
                environment_id="candidate-env", implementation=ContentReference(type="generated", ref="candidate-env"),
                tools=[ToolContract(name="work", interface="builtin")], maturity="generated_contract",
            ),
            artifacts=[ArtifactRequirement(path="artifact://result", description="result")],
            scoring=ScoringContract(dimensions=[
                ScoringDimensionContract(name="completion", weight=100, description="done", evidence_sources=["artifact"])
            ]),
        ),
        source_refs=[SourceRef(source_mode=SourceMode.GENERATED_ENVIRONMENT, source_id="rag-environment-blueprint", sample_id="b")],
    )


def test_generated_environment_is_staged_not_promoted(tmp_path):
    benchmark = Benchmark(benchmark_id="b", user_goal=UserGoal(goal_id="g", description="x", target_size=1), items=[generated_item()])
    registry = EnvironmentCandidateRegistry(tmp_path / "candidates")
    candidate = stage_generated_candidates(benchmark, registry)[0]
    assert candidate.status.value == "generated_contract"
    assert not candidate.readiness().ready
    assert "human approval required" in candidate.readiness().blockers
    with pytest.raises(ValueError, match="not promotion-ready"):
        registry.build_promotion_bundle(candidate.candidate_id)
    assert not (tmp_path / "agent-octagon-envs").exists()


def test_candidate_requires_checks_pilots_agent_eval_and_human_approval(tmp_path):
    benchmark = Benchmark(benchmark_id="b", user_goal=UserGoal(goal_id="g", description="x", target_size=1), items=[generated_item()])
    registry = EnvironmentCandidateRegistry(tmp_path / "candidates")
    candidate = registry.stage(benchmark, benchmark.items[0])
    for check_id, stage in [
        ("agent_subject_construct", "static"), ("scenario_completeness", "static"),
        ("contract_schema", "static"), ("agent_octagon_abi", "static"), ("provenance_safety", "static"),
        ("scaffold_integrity", "scaffold"), ("scorer_semantic_review", "scoring"),
        ("environment_smoke", "smoke"), ("scorer_smoke", "smoke"), ("artifact_collection", "runtime"),
    ]:
        candidate = registry.record_check(candidate.candidate_id, CandidateCheck(check_id=check_id, stage=stage, status="passed"))
    candidate = registry.record_pilot(candidate.candidate_id, PilotTrial(trial_ref="trial/a", agent_id="agent-a", score=75, passed=True))
    candidate = registry.record_pilot(candidate.candidate_id, PilotTrial(trial_ref="trial/b", agent_id="agent-b", score=80, passed=True))
    candidate = registry.record_agent_eval(candidate.candidate_id, AgentEvalReportRef(
        report_ref="agent-eval/report.json", benchmark_quality_score=75,
        human_alignment_score=72, difficulty_score=65,
    ))
    assert not candidate.readiness().ready
    candidate = registry.approve(candidate.candidate_id, HumanApproval(approver="reviewer", approved=True))
    assert candidate.readiness().ready
    assert candidate.status.value == "promotion_ready"
    bundle = registry.build_promotion_bundle(candidate.candidate_id)
    assert bundle.name == "promotion-bundle.json"


def test_scaffold_bundle_writes_complete_runnable_shape(tmp_path):
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, write_scaffold

    benchmark = Benchmark(benchmark_id="b", user_goal=UserGoal(goal_id="g", description="x", target_size=1), items=[generated_item()])
    registry = EnvironmentCandidateRegistry(tmp_path / "candidates")
    candidate = registry.stage(benchmark, benchmark.items[0])
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="name: candidate-env\ntype: coding\ncategory: agent-system\npass_threshold: 60\ndimensions:\n  - name: completion\n    weight: 100\n    description: done\n"),
        ScaffoldFile(path="core.py", content=(
            "from octagon.env_api import EnvContext, env_tool\n"
            "@env_tool(name='work', description='work', parameters={'type':'object','properties':{}})\n"
            "def work(ctx: EnvContext):\n    return {'ok': True}\n"
        )),
        ScaffoldFile(path="mcp_server.py", content=(
            "import os, httpx\nfrom mcp.server.fastmcp import FastMCP\n"
            "mcp=FastMCP('test')\n"
            "ATTEMPT_ID=os.environ.get('OCTAGON_ATTEMPT_ID','')\n"
            "TOKEN=os.environ.get('OCTAGON_ENV_TOKEN','')\n"
            "BASE=os.environ.get('OCTAGON_BASE_URL','')\n"
            "@mcp.tool()\ndef work():\n"
            "    return httpx.post(f'{BASE}/attempts/{ATTEMPT_ID}/tools/work', headers={'Authorization':f'Bearer {TOKEN}'}, json={}).json()\n"
        )),
        ScaffoldFile(path="scorer.py", content=(
            "from pathlib import Path\n"
            "def score(*, attempt_id, task, env_db=None, trace=None, final_state=None, **kwargs):\n"
            "    workspace = Path(env_db).parent / 'skill_workspace' if env_db else Path('missing')\n"
            "    called = any(isinstance(row, dict) and row.get('tool_name') == 'work' for row in (trace or []))\n"
            "    return [{'dimension':'completion','value':0,'detail':f'{attempt_id}:{workspace}:{called}'}]\n"
        )),
        ScaffoldFile(path="tasks/task_001.json", content='{"id":"task_001","env_name":"candidate-env","prompt":"do the task","timeout_seconds":600}'),
        ScaffoldFile(path="README.md", content="# candidate-env\n"),
        ScaffoldFile(path="tests/test_environment.py", content="def test_shape():\n    assert True\n"),
    ])
    root, validation = write_scaffold(registry, candidate.candidate_id, bundle)
    assert validation.valid
    assert (root / "meta.yaml").is_file()
    assert (root / "scorer.py").is_file()
    assert (root / "tasks/task_001.json").is_file()
    assert registry.load(candidate.candidate_id).status.value == "scaffolded"
    assert not registry.load(candidate.candidate_id).readiness().ready


def test_static_validation_warns_on_semantic_scorer_risks_for_agent_review(tmp_path):
    from benchmark_forge.domain import AgentCapabilityRequirement, CoordinationContract, DelegatedSubtaskContract
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, validate_scaffold

    item = generated_item()
    item.executable_task.agent_capabilities = [AgentCapabilityRequirement(name="subagent_spawn")]
    item.executable_task.coordination = CoordinationContract(subtasks=[
        DelegatedSubtaskContract(subtask_id="hidden_internal_id", objective="work")
    ])
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="name: candidate-env\ntype: coding\ncategory: agent-system\ndimensions:\n  - name: completion\n    weight: 100\n    description: done\n"),
        ScaffoldFile(path="core.py", content=""),
        ScaffoldFile(path="scorer.py", content="def score(*, attempt_id, task, env_db=None, **kw):\n    return [{'dimension':'completion','value':100 if 'hidden_internal_id' else 0,'detail':'x'}]\n"),
        ScaffoldFile(path="tasks/t.json", content='{"id":"t","env_name":"candidate-env","prompt":"spawn one reviewer","timeout_seconds":60}'),
    ])
    result = validate_scaffold(bundle, item)
    assert not result.valid
    assert any("canonical attempt evidence" in warning for warning in result.warnings)
    assert any("hidden coordination node IDs" in error for error in result.errors)


def scorer_design():
    from benchmark_forge import ScorerCalibrationCase, ScorerDesign, ScorerImplementationOption

    return ScorerDesign(
        scoring_objective="Measure completion from observable evidence without trusting self-report alone.",
        public_contract_rules=["Do not require identifiers absent from the public task."],
        workspace_resolution_options=["resolve through env_db and attempt_id", "use a supplied final_state workspace reference"],
        evidence_precedence=["runtime_canonical", "artifact_observed", "agent_self_report"],
        implementation_options=[
            ScorerImplementationOption(
                option_id="artifact-structure", dimension_name="completion", evidence_source="artifact",
                authority="artifact_observed", strategy="parse the required result artifact",
                required_inputs=["artifact://result"], observable_success="required fields are present",
                observable_failure="artifact is absent or malformed", fallback_rank=0,
            ),
            ScorerImplementationOption(
                option_id="runtime-state", dimension_name="completion", evidence_source="environment_state",
                authority="runtime_canonical", strategy="read completion state associated with attempt_id",
                required_inputs=["attempt_id", "env_db"], observable_success="runtime records completion",
                observable_failure="runtime has no completion record", fallback_rank=1,
            ),
        ],
        calibration_cases=[
            ScorerCalibrationCase(case_id="fabricated-log", description="Agent claims completion without output", expected_behavior="low score", protects_against=["self-report gaming"])
        ],
    )


def test_scorer_design_requires_multiple_implementation_options_per_dimension():
    from pydantic import ValidationError
    from benchmark_forge import ScorerDesign, ScorerImplementationOption

    with pytest.raises(ValidationError, match="at least two implementation options"):
        ScorerDesign(
            scoring_objective="x",
            implementation_options=[ScorerImplementationOption(
                option_id="only", dimension_name="completion", evidence_source="artifact",
                authority="artifact_observed", strategy="read file", observable_success="exists",
                observable_failure="missing",
            )],
        )


def test_registry_records_agent_scorer_design_and_semantic_review(tmp_path):
    from benchmark_forge import ScorerDimensionFinding, ScorerReview

    benchmark = Benchmark(benchmark_id="b", user_goal=UserGoal(goal_id="g", description="x", target_size=1), items=[generated_item()])
    registry = EnvironmentCandidateRegistry(tmp_path / "candidates")
    candidate = registry.stage(benchmark, benchmark.items[0])
    registry.record_scorer_design(candidate.candidate_id, scorer_design())
    repair = ScorerReview(
        verdict="repair", summary="Scorer trusts a self-authored log and cannot locate the attempt workspace.",
        dimension_findings=[ScorerDimensionFinding(
            dimension_name="completion", covered=True, publicly_satisfiable=True, runtime_grounded=False,
            findings=["workspace resolution missing"],
        )],
        repair_instructions=["Resolve artifacts via attempt_id/env_db and use the runtime record as primary evidence."],
    )
    candidate = registry.record_scorer_review(candidate.candidate_id, repair)
    assert candidate.status.value == "needs_repair"
    assert next(check for check in candidate.checks if check.check_id == "scorer_semantic_review").status == "failed"

    passed = ScorerReview(
        verdict="pass", summary="Runtime and artifact evidence are now combined with a documented fallback.",
        dimension_findings=[ScorerDimensionFinding(
            dimension_name="completion", covered=True, publicly_satisfiable=True, runtime_grounded=True,
            selected_option_ids=["runtime-state", "artifact-structure"],
        )],
    )
    candidate = registry.record_scorer_review(candidate.candidate_id, passed)
    assert candidate.status.value != "needs_repair"
    assert len(candidate.scorer_reviews) == 2
    assert (registry._path(candidate.candidate_id).parent / "validation" / "scorer-design.json").is_file()
    assert (registry._path(candidate.candidate_id).parent / "validation" / "scorer-review-2.json").is_file()


def test_scorer_repair_bundle_is_overlay_and_cannot_drop_unchanged_files():
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, merge_scaffold_bundles

    base = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="core.py", content='"""empty core"""'),
        ScaffoldFile(path="scorer.py", content="old"),
        ScaffoldFile(path="tasks/t.json", content="{}"),
    ])
    repair = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="scorer.py", content="new"),
        ScaffoldFile(path="tests/test_scorer.py", content="def test_score(): pass"),
    ])
    merged = merge_scaffold_bundles(base, repair)
    files = {file.path: file.content for file in merged.files}
    assert files["core.py"] == '"""empty core"""'
    assert files["scorer.py"] == "new"
    assert "tasks/t.json" in files
    assert "tests/test_scorer.py" in files


def test_successful_scaffold_check_cannot_mask_failed_semantic_review(tmp_path):
    benchmark = Benchmark(benchmark_id="b", user_goal=UserGoal(goal_id="g", description="x", target_size=1), items=[generated_item()])
    registry = EnvironmentCandidateRegistry(tmp_path / "candidates")
    candidate = registry.stage(benchmark, benchmark.items[0])
    registry.record_check(candidate.candidate_id, CandidateCheck(
        check_id="scorer_semantic_review", stage="scoring", status="failed", summary="invalid construct"
    ))
    candidate = registry.record_check(candidate.candidate_id, CandidateCheck(
        check_id="scaffold_integrity", stage="scaffold", status="passed", summary="files exist"
    ))
    assert candidate.status.value == "needs_repair"


def test_ir_expressiveness_failure_is_distinct_from_repair(tmp_path):
    benchmark = Benchmark(benchmark_id="b", user_goal=UserGoal(goal_id="g", description="x", target_size=1), items=[generated_item()])
    registry = EnvironmentCandidateRegistry(tmp_path / "candidates")
    candidate = registry.stage(benchmark, benchmark.items[0])
    candidate = registry.record_check(candidate.candidate_id, CandidateCheck(
        check_id="ir_expressiveness", stage="static", status="failed",
        summary="requires coordination_graph", evidence_refs=[],
    ))
    assert candidate.status.value == "requires_ir_extension"
    assert "requires_ir_extension" in candidate.status.value


def test_normalize_flat_bundle_repairs_package_style_mcp_entrypoint():
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, normalize_octagon_scaffold
    item = generated_item()
    item = item.model_copy(update={"executable_task": item.executable_task.model_copy(update={
        "environment": item.executable_task.environment.model_copy(update={
            "entrypoints": {"mcp": {"command": ["python", "-m", "fake_env.mcp_server"]}}
        })
    })})
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="entrypoints:\n  mcp:\n    command: [python, -m, fake_env.mcp_server]\n"),
        ScaffoldFile(path="mcp_server.py", content=""),
        ScaffoldFile(path="tests/test_protocol.py", content='MODULE = "fake_env.mcp_server"\n'),
        ScaffoldFile(path="tasks/task.json", content="{}"),
    ])
    normalized = normalize_octagon_scaffold(bundle, item)
    files = {f.path: f.content for f in normalized.files}
    assert "- mcp_server" in files["meta.yaml"]
    assert "fake_env.mcp_server" not in files["tests/test_protocol.py"]
    assert 'MODULE = "mcp_server"' in files["tests/test_protocol.py"]


def test_validate_scaffold_rejects_brittle_source_text_test():
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, validate_scaffold
    item = generated_item()
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="name: candidate-env\ndimensions:\n  - name: completion\n"),
        ScaffoldFile(path="core.py", content=""),
        ScaffoldFile(path="scorer.py", content="def score(**kwargs): return []"),
        ScaffoldFile(path="tasks/task.json", content='{"id":"task","env_name":"candidate-env","prompt":"x","timeout_seconds":10}'),
        ScaffoldFile(path="tests/test_source.py", content='source = Path("mcp_server.py").read_text()\nassert "literal" in source\n'),
    ])
    result = validate_scaffold(bundle, item)
    assert not result.valid
    assert any("implementation source text" in error for error in result.errors)


def test_rejected_rubric_integrity_blocks_component_materialization(tmp_path):
    from benchmark_forge.environment_ir import lower_contract_to_ir
    from benchmark_forge.rubric_review import RubricCriterionReview, RubricIntegrityReview
    from benchmark_forge.service import materialize_candidates_with_scorer_control

    benchmark = Benchmark(benchmark_id="b", user_goal=UserGoal(goal_id="g", description="x", target_size=1), items=[generated_item()])
    registry = EnvironmentCandidateRegistry(tmp_path / "candidates")
    candidate = registry.stage(benchmark, benchmark.items[0])
    registry.record_environment_ir(candidate.candidate_id, lower_contract_to_ir(candidate.item.executable_task).freeze())
    review = RubricIntegrityReview(
        verdict="reject", target_alignment="drifted", summary="rubric measures formatting instead of completion",
        global_findings=["target reversed"], criterion_reviews=[RubricCriterionReview(
            criterion_id="completion", target_covered=False, scope="too_narrow",
            direction="reversed", evidence_plausible=False,
        )],
    )
    registry.record_rubric_integrity_review(candidate.candidate_id, review)

    class Agents:
        calls = 0
        def materialize_environment_components(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("rejected rubric must block codegen")

    agents = Agents()
    materialize_candidates_with_scorer_control(
        agents=agents, benchmark=benchmark, registry=registry, candidates=[candidate], require_frozen_ir=True,
    )
    assert agents.calls == 0
    loaded = registry.load(candidate.candidate_id)
    assert loaded.status.value == "needs_repair"
    assert any(check.check_id == "rubric_integrity" and check.status == "failed" for check in loaded.checks)
    assert list((tmp_path / "candidates" / candidate.candidate_id / "validation").glob("rubric-integrity-review-*.json"))


def test_tests_component_prompt_forbids_source_text_assertions():
    from benchmark_forge.component_agents import _COMPONENT_INSTRUCTIONS

    instructions = _COMPONENT_INSTRUCTIONS["tests"]
    assert "inspect.getsource" in instructions
    assert "Never read implementation files" in instructions
    assert "calling their public functions or entrypoints" in instructions
    assert "score(attempt_id=..., task=..." in instructions
    assert "Do not guess positional" in instructions
    assert "list of dimension/value/detail records" in instructions
    assert "do not require a" in instructions
    assert "separate Python function for every tool" in instructions


def test_normalize_with_frozen_ir_preserves_public_interface_and_revised_rubric():
    import yaml

    from benchmark_forge.environment_ir import lower_contract_to_ir
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, normalize_octagon_scaffold

    item = generated_item()
    draft = lower_contract_to_ir(item.executable_task)
    revised = draft.model_copy(update={
        "rubric": draft.rubric.model_copy(update={
            "criteria": [draft.rubric.criteria[0].model_copy(update={"description": "revised criterion"})]
        })
    })
    ir = revised.freeze()
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="dimensions:\n  - name: completion\n    description: stale contract criterion\n"),
        ScaffoldFile(path="core.py", content=""),
        ScaffoldFile(path="scorer.py", content="def score(**kwargs): return []"),
        ScaffoldFile(path="tasks/t.json", content="{}"),
    ])

    normalized = normalize_octagon_scaffold(bundle, item, ir)
    files = {file.path: file.content for file in normalized.files}
    meta = yaml.safe_load(files["meta.yaml"])

    assert meta["task_id"] == ir.task_id
    assert meta["protocol"] == ir.protocol
    assert [tool["tool_id"] for tool in meta["tools"]] == [tool.tool_id for tool in ir.tools]
    assert [artifact["artifact_id"] for artifact in meta["artifacts"]] == [artifact.artifact_id for artifact in ir.artifacts]
    assert meta["dimensions"][0]["description"] == "revised criterion"


def test_validate_scaffold_rejects_environment_simulation_of_agent_native_capability():
    from benchmark_forge.domain import AgentCapabilityRequirement, ToolContract
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, validate_scaffold

    item = generated_item()
    item.executable_task.agent_capabilities = [AgentCapabilityRequirement(name="subagent_spawn")]
    item.executable_task.environment.tools = [ToolContract(
        name="subagent_spawn", ownership="benchmark_environment", interface="builtin",
        description="fake child agent",
    )]
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="name: candidate-env\ndimensions:\n  - name: completion\n"),
        ScaffoldFile(path="core.py", content="def _subagent_spawn(args): return {'status': 'completed'}"),
        ScaffoldFile(path="scorer.py", content="def score(**kwargs): return []"),
        ScaffoldFile(path="tasks/t.json", content='{"id":"t","env_name":"candidate-env","prompt":"delegate","timeout_seconds":10}'),
    ])
    result = validate_scaffold(bundle, item)
    assert not result.valid
    assert any("reimplements Agent-native capabilities" in error for error in result.errors)


def test_validate_scaffold_rejects_runtime_implementation_of_agent_owned_tool():
    from benchmark_forge.domain import AgentCapabilityRequirement, ToolContract
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, validate_scaffold

    item = generated_item()
    item.executable_task.agent_capabilities = [AgentCapabilityRequirement(name="subagent_spawn")]
    item.executable_task.environment.tools = [ToolContract(
        name="subagent_spawn", ownership="agent_runtime", interface="builtin",
    )]
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="name: candidate-env\ndimensions:\n  - name: completion\n"),
        ScaffoldFile(path="core.py", content="def subagent_spawn(args): return {}"),
        ScaffoldFile(path="scorer.py", content="def score(**kwargs): return []"),
        ScaffoldFile(path="tasks/t.json", content='{"id":"t","env_name":"candidate-env","prompt":"delegate","timeout_seconds":10}'),
    ])
    result = validate_scaffold(bundle, item)
    assert not result.valid
    assert any("implements tools owned by agent_runtime" in error for error in result.errors)


def test_role_prompts_define_complete_agent_as_evaluation_subject():
    from benchmark_forge.pydantic_agents import PydanticAIRoleAgents

    assert "complete Agent, not a bare LLM" in PydanticAIRoleAgents.design_instructions
    assert "synthetic subagent_spawn" in PydanticAIRoleAgents.design_instructions
    assert "complete Agent, not a prompt-only LLM" in PydanticAIRoleAgents.executor_instructions
    assert "do not implement them as synthetic" in PydanticAIRoleAgents.executor_instructions
    assert "reimplements an Agent-native" in PydanticAIRoleAgents.verification_instructions


def test_validate_scaffold_rejects_synthetic_memory_for_native_context_benchmark():
    from benchmark_forge.domain import ToolContract
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, validate_scaffold

    item = generated_item()
    item.dimension_id = "context_compression_fidelity"
    item.covered_dimension_ids = ["context_compression_fidelity"]
    item.executable_task.environment.tools = [
        ToolContract(name="read_document", ownership="benchmark_environment", interface="builtin"),
        ToolContract(name="memory_write", ownership="benchmark_environment", interface="builtin"),
        ToolContract(name="memory_read", ownership="benchmark_environment", interface="builtin"),
    ]
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="name: candidate-env\ndimensions:\n  - name: completion\n"),
        ScaffoldFile(path="core.py", content=""),
        ScaffoldFile(path="scorer.py", content="def score(**kwargs): return []"),
        ScaffoldFile(path="tasks/t.json", content='{"id":"t","env_name":"candidate-env","prompt":"compress","timeout_seconds":10}'),
    ])
    result = validate_scaffold(bundle, item)
    assert not result.valid
    assert any("substitutes synthetic memory/context tools" in error for error in result.errors)


def test_delegation_construct_rejects_agent_callable_fault_injector():
    from benchmark_forge.domain import ToolContract
    from benchmark_forge.staging import validate_agent_subject_contract

    item = generated_item()
    item.dimension_id = "delegation_quality"
    item.covered_dimension_ids = ["delegation_quality"]
    item.executable_task.environment.tools = [ToolContract(
        name="failure_conflict_injector.apply",
        ownership="benchmark_environment",
        interface="mcp",
    )]
    errors = validate_agent_subject_contract(item)
    assert any("injectors must be controlled by evaluation_system" in error for error in errors)


def test_component_prompt_includes_generated_dependency_interfaces():
    from benchmark_forge.component_agents import _prompt
    from benchmark_forge.environment_ir import IRComponentFile, IRComponentOutput, lower_contract_to_ir

    item = generated_item()
    ir = lower_contract_to_ir(item.executable_task).freeze()
    runtime = IRComponentOutput(component_id="runtime", files=[
        IRComponentFile(path="mcp_server.py", content="def handle(request): return {}")
    ])
    prompt = _prompt("tests", item, ir, None, [runtime])
    assert "Already generated dependency components" in prompt
    assert "def handle(request)" in prompt


def test_static_validation_accepts_scorer_using_supplied_trace_and_final_state():
    from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, validate_scaffold

    item = generated_item()
    bundle = EnvironmentScaffoldBundle(environment_id="candidate-env", files=[
        ScaffoldFile(path="meta.yaml", content="name: candidate-env\ndimensions:\n  - name: completion\n"),
        ScaffoldFile(path="core.py", content=""),
        ScaffoldFile(path="scorer.py", content="def score(*, attempt_id, task, env_db=None, trace=None, final_state=None, **kwargs):\n    return [{'dimension':'completion','value':1 if trace or final_state else 0,'detail':'evidence'}]"),
        ScaffoldFile(path="tasks/t.json", content='{"id":"t","env_name":"candidate-env","prompt":"x","timeout_seconds":10}'),
    ])
    result = validate_scaffold(bundle, item)
    assert not any("canonical attempt evidence" in warning for warning in result.warnings)
