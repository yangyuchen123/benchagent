from __future__ import annotations

import json
import pytest

from benchmark_forge import BenchmarkOrchestrator, RunConfig, UserGoal
from benchmark_forge.domain import BenchmarkGrounding, GroundingStatus, SourceMode
from benchmark_forge.persistence import load_benchmark
from benchmark_forge.providers import DatasetProvider, ProceduralProvider
from benchmark_forge.reducers import StateError, apply_design
from benchmark_forge.actions import AllocationDecision, DesignAction
from benchmark_forge.domain import Allocation
from benchmark_forge.reducers import apply_allocation


def test_happy_path_with_procedural_provider(tmp_path):
    goal = UserGoal(goal_id="t1", description="test simple reasoning", target_size=4)
    benchmark = BenchmarkOrchestrator(config=RunConfig()).run(
        goal,
        [ProceduralProvider(capacity_hint=4)],
        benchmark_id="b1",
        artifact_root=tmp_path,
    )
    assert benchmark.status.value in {"degraded", "completed", "partial"}
    assert len(benchmark.items) == 4
    assert benchmark.items[0].source_mode == SourceMode.SYNTHETIC
    assert (tmp_path / "benchmark.json").exists()
    assert len(load_benchmark(tmp_path / "benchmark.json").events) >= 1


def test_empty_pool_produces_blocked_draft():
    goal = UserGoal(goal_id="t2", description="need unavailable audio reasoning", target_size=3)
    benchmark = BenchmarkOrchestrator().run(goal, [], benchmark_id="b2")
    assert benchmark.status.value == "partial"
    assert benchmark.dimensions
    assert benchmark.groundings[0].status == GroundingStatus.BLOCKED
    assert not benchmark.items
    assert any("no source provider" in str(g.reasons) for g in benchmark.groundings)


def test_existing_dataset_items_are_traceable():
    provider = DatasetProvider("fixture", [
        {"question": "What color?", "answer": "blue", "context": "The sky is blue."},
        {"question": "What number?", "answer": "two", "context": "There are two cats."},
    ])
    goal = UserGoal(goal_id="t3", description="use fixture", target_size=5)
    benchmark = BenchmarkOrchestrator().run(goal, [provider], benchmark_id="b3")
    assert len(benchmark.items) == 2
    assert any("shortfall" in w for w in benchmark.warnings)
    for item in benchmark.items:
        assert item.source_refs
        assert item.source_refs[0].source_id == "fixture"


def test_reducer_rejects_duplicate_dimension():
    goal = UserGoal(goal_id="t4", description="x", target_size=1)
    from benchmark_forge import Benchmark
    benchmark = BenchmarkOrchestrator().create(goal, benchmark_id="b4")
    action = DesignAction(action="add_dimension", dimension={
        "id": "d", "name": "D", "description": "d"
    })
    apply_design(benchmark, action)
    try:
        apply_design(benchmark, action)
    except StateError:
        pass
    else:
        raise AssertionError("duplicate dimension must be rejected")


def test_artifact_is_json_serializable(tmp_path):
    goal = UserGoal(goal_id="t5", description="serializable", target_size=1)
    benchmark = BenchmarkOrchestrator().run(goal, [ProceduralProvider(capacity_hint=1)], artifact_root=tmp_path)
    data = json.loads((tmp_path / "benchmark.json").read_text())
    assert data["benchmark_id"] == benchmark.benchmark_id
    assert len(data["events"]) >= 1


def test_pydantic_ai_validates_role_output():
    from pydantic_ai.models.test import TestModel
    from benchmark_forge.actions import DesignAction
    from benchmark_forge.pydantic_ai_adapter import PydanticAIRunner

    runner = PydanticAIRunner(
        model=TestModel(custom_output_args={"action": "finish_design", "rationale": "test"}),
        output_type=DesignAction,
        instructions="Return a DesignAction",
    )
    result = runner.run_sync("finish")
    assert result.action == "finish_design"


def test_pydantic_graph_runs_original_five_stages():
    from benchmark_forge import GraphDeps, BenchmarkOrchestrator, run_graph_sync
    goal = UserGoal(goal_id="graph", description="graph reasoning", target_size=2)
    orchestrator = BenchmarkOrchestrator()
    benchmark = orchestrator.create(goal, benchmark_id="graph-benchmark")
    result = run_graph_sync(benchmark, GraphDeps(orchestrator, [ProceduralProvider(capacity_hint=2)]))
    assert result.benchmark_id == "graph-benchmark"
    assert len(result.items) == 2
    roles = {event.role for event in result.events}
    assert {"design", "grounding", "allocation", "executor", "verification", "orchestrator"} <= roles


def test_multiple_sources_fill_allocation_shortfall():
    from benchmark_forge import BenchmarkOrchestrator
    goal = UserGoal(goal_id="multi", description="use multiple sources", target_size=3)
    providers = [
        DatasetProvider("small", [{"question": "q1", "answer": "a1", "context": "c1"}]),
        DatasetProvider("backup", [
            {"question": "q2", "answer": "a2", "context": "c2"},
            {"question": "q3", "answer": "a3", "context": "c3"},
        ]),
    ]
    benchmark = BenchmarkOrchestrator().run(goal, providers, benchmark_id="multi")
    assert len(benchmark.items) == 3
    assert {item.source_id for item in benchmark.items} == {"small", "backup"}
    assert len({(ref.source_id, ref.sample_id) for item in benchmark.items for ref in item.source_refs}) == 3


def test_checkpoint_resume_from_finalize_stage(tmp_path):
    goal = UserGoal(goal_id="resume", description="resume me", target_size=2)
    orchestrator = BenchmarkOrchestrator()
    first = orchestrator.run(
        goal,
        [ProceduralProvider(capacity_hint=2)],
        benchmark_id="resume-benchmark",
        artifact_root=tmp_path,
    )
    checkpoint = tmp_path / "checkpoint.json"
    assert checkpoint.exists()
    resumed = BenchmarkOrchestrator().run(
        UserGoal(goal_id="ignored", description="ignored", target_size=99),
        [ProceduralProvider(capacity_hint=2)],
        resume_from=str(checkpoint),
        artifact_root=tmp_path / "resumed",
    )
    assert resumed.benchmark_id == first.benchmark_id
    assert len(resumed.items) == 2
    assert any(event.event_type == "checkpoint_resumed" for event in resumed.events)


def test_allocation_reducer_trims_global_target_overflow():
    from benchmark_forge import BenchmarkOrchestrator
    goal = UserGoal(goal_id="overflow", description="quota", target_size=2)
    benchmark = BenchmarkOrchestrator().create(goal, benchmark_id="overflow")
    apply_design(benchmark, DesignAction(action="add_dimension", dimension={
        "id": "d", "name": "D", "description": "d"
    }))
    apply_allocation(benchmark, AllocationDecision(
        action="set_allocation",
        allocations=[
            Allocation(dimension_id="d", source_mode=SourceMode.SYNTHETIC, source_id="p1", executable_quota=2),
            Allocation(dimension_id="d", source_mode=SourceMode.SYNTHETIC, source_id="p2", executable_quota=2),
        ],
    ))
    assert sum(a.total_quota for a in benchmark.allocations) == 2
    assert any("overflow trimmed" in warning for warning in benchmark.warnings)


def test_executor_failure_triggers_bounded_replenishment():
    from benchmark_forge.agents import DeterministicMVPAgents

    class FailFirstExecutor(DeterministicMVPAgents):
        failed = False

        def execute(self, benchmark, allocation, provider, sample):
            if not self.failed:
                self.failed = True
                raise RuntimeError("synthetic transient executor failure")
            return super().execute(benchmark, allocation, provider, sample)

    goal = UserGoal(goal_id="replenish", description="recover one failed sample", target_size=1)
    benchmark = BenchmarkOrchestrator(agents=FailFirstExecutor()).run(
        goal,
        [ProceduralProvider(capacity_hint=2)],
        benchmark_id="replenish",
    )
    assert len(benchmark.items) == 1
    assert benchmark.items[0].source_refs[0].sample_id == "1"
    assert any(event.event_type == "replenishment_requested" for event in benchmark.events)
    assert any(event.event_type == "replenishment_round" for event in benchmark.events)


def test_octagon_profile_is_read_only_and_structured(tmp_path):
    from benchmark_forge.octagon import load_environment_profile

    env = tmp_path / "travel-planner"
    (env / "tasks").mkdir(parents=True)
    (env / "tasks" / "travel_001.json").write_text('{"instruction":"book"}', encoding="utf-8")
    (env / "meta.yaml").write_text(
        """name: travel-planner
schema_version: '1.0'
type: skill
category: baseline
test_focus: budget and dates
description: mock travel task
pass_threshold: 60
prerequisites:
  level: none
  requires: []
entrypoints:
  mcp:
    enabled: true
dimensions:
  - name: task_completion
    weight: 40
    description: complete booking
""",
        encoding="utf-8",
    )
    profile = load_environment_profile(env)
    assert profile.env_id == "travel-planner"
    assert profile.task_paths == ["tasks/travel_001.json"]
    assert profile.agent_summary()["dimensions"][0]["name"] == "task_completion"
    assert "eval-system" in profile.agent_summary()["execution_boundary"]
    assert not (env / "generated").exists()


def test_octagon_catalog_search_and_agent_context(tmp_path):
    from benchmark_forge.octagon import EnvironmentCatalog

    for name, category, focus in [
        ("travel-planner", "baseline", "budget and booking constraints"),
        ("pytest-repair", "coding", "repair a compiler regression"),
    ]:
        env = tmp_path / name
        (env / "tasks").mkdir(parents=True)
        (env / "tasks" / "one.json").write_text("{}", encoding="utf-8")
        (env / "meta.yaml").write_text(
            f"name: {name}\ntype: coding\ncategory: {category}\ntest_focus: {focus}\n"
            "prerequisites:\n  level: none\ndimensions:\n"
            "  - name: task_completion\n    weight: 100\n    description: done\n",
            encoding="utf-8",
        )
    catalog = EnvironmentCatalog(tmp_path)
    assert [p.env_id for p in catalog.search("compiler regression")] == ["pytest-repair"]
    context = catalog.agent_context(query="booking")
    assert context["profiles"][0]["env_id"] == "travel-planner"
    assert context["interpretation"]["precedent"].startswith("Existing dimensions")
    assert catalog.list_tasks("travel-planner")[0]["task_id"] == "travel-planner:one"


def test_octagon_knowledge_base_indexes_safe_files_and_retrieves(tmp_path):
    from benchmark_forge.octagon import EnvironmentCatalog, OctagonKnowledgeBase

    env = tmp_path / "safe-env"
    (env / "tasks").mkdir(parents=True)
    (env / "private").mkdir(parents=True)
    (env / "meta.yaml").write_text(
        "name: safe-env\ntype: skill\ncategory: coding\ntest_focus: parallel scheduling\n"
        "dimensions:\n  - name: scheduling\n    weight: 100\n    description: schedule tasks in parallel\n",
        encoding="utf-8",
    )
    (env / "README.md").write_text("This benchmark tests parallel scheduling and dependency ordering.", encoding="utf-8")
    (env / "tasks" / "task.json").write_text('{"instruction": "schedule independent jobs"}', encoding="utf-8")
    (env / "private" / "expected.json").write_text("SECRET_EXPECTED_ANSWER", encoding="utf-8")

    kb = OctagonKnowledgeBase(tmp_path / "kb.sqlite3")
    count = kb.index_catalog(EnvironmentCatalog(tmp_path))
    assert count >= 3
    results = kb.search("parallel scheduling")
    assert results
    assert all("private" not in result.source_path for result in results)
    context = kb.context("schedule jobs", role="executor")
    assert context["role"] == "executor"
    assert context["results"]
    assert "eval-system" in context["instruction"]
    assert kb.count() == count


def test_octagon_knowledge_base_does_not_mount_source_tree(tmp_path):
    from benchmark_forge.octagon import EnvironmentCatalog, OctagonKnowledgeBase

    env = tmp_path / "env"
    env.mkdir()
    (env / "meta.yaml").write_text("name: env\ntype: coding\n", encoding="utf-8")
    source = EnvironmentCatalog(tmp_path)
    kb = OctagonKnowledgeBase(tmp_path / "knowledge" / "index.db")
    kb.index_catalog(source)
    assert not (tmp_path / "knowledge" / "env").exists()
    assert all(path.name != "core.py" for path in (tmp_path / "knowledge").rglob("*"))


def test_octagon_knowledge_context_can_exclude_material_noise(tmp_path):
    from benchmark_forge.octagon import EnvironmentCatalog, OctagonKnowledgeBase

    env = tmp_path / "env"
    (env / "tasks").mkdir(parents=True)
    (env / "materials").mkdir(parents=True)
    (env / "meta.yaml").write_text("name: env\ntype: coding\ntest_focus: compiler repair\n", encoding="utf-8")
    (env / "tasks" / "task.json").write_text('{"instruction":"repair compiler"}', encoding="utf-8")
    (env / "materials" / "large.txt").write_text("compiler compiler compiler", encoding="utf-8")
    kb = OctagonKnowledgeBase(tmp_path / "kb.db")
    kb.index_catalog(EnvironmentCatalog(tmp_path))
    results = kb.context("compiler", source_kinds=["environment_profile", "task_spec"])['results']
    assert results
    assert all(item["source_kind"] in {"environment_profile", "task_spec"} for item in results)


def test_executable_task_cannot_be_compressed_to_multiple_choice():
    import pytest
    from benchmark_forge import BenchmarkItem, SourceMode
    from benchmark_forge.domain import ContentReference, EnvironmentContract, ExecutableTaskContract, ScoringContract, ScoringDimensionContract, ToolContract

    task = ExecutableTaskContract(
        task_id="parallel-1",
        instruction="Use the provided tools to complete the dependency DAG and submit a report.",
        environment=EnvironmentContract(
            environment_id="parallel-env",
            implementation=ContentReference(type="path", ref="agent-parallel-scheduling"),
            tools=[ToolContract(name="fetch_quote", interface="mcp")],
            maturity="existing",
        ),
        scoring=ScoringContract(dimensions=[
            ScoringDimensionContract(name="parallelism", weight=100, description="overlapping calls", evidence_sources=["tool_trace"])
        ]),
    )
    with pytest.raises(ValueError, match="must not be represented as multiple choice"):
        BenchmarkItem(
            item_id="i", dimension_id="d", source_mode=SourceMode.EXISTING_ENVIRONMENT,
            source_id="octagon", item_kind="executable_task", answer_type="multiple_choice",
            options=["A", "B"], answer="A", executable_task=task,
        )


def test_octagon_environment_provider_exposes_task_contract_without_mount(tmp_path):
    from benchmark_forge.octagon import EnvironmentCatalog, OctagonEnvironmentProvider
    from benchmark_forge.domain import SourceMode

    env = tmp_path / "tool-env"
    (env / "tasks").mkdir(parents=True)
    (env / "meta.yaml").write_text(
        "name: tool-env\ntype: skill\ncategory: agent-system\ntest_focus: tool use\n"
        "entrypoints:\n  mcp:\n    enabled: true\ndimensions:\n"
        "  - name: completion\n    weight: 100\n    description: done\n",
        encoding="utf-8",
    )
    (env / "tasks" / "one.json").write_text('{"id":"one","prompt":"use tools","constraints":{"x":1}}', encoding="utf-8")
    provider = OctagonEnvironmentProvider(EnvironmentCatalog(tmp_path), ["tool-env"])
    assert provider.source_mode == SourceMode.EXISTING_ENVIRONMENT
    sample = provider.sample(1)[0]
    assert sample.fields["task_spec"]["prompt"] == "use tools"
    assert sample.fields["environment_ref"]["ref"] == "tool-env"
    assert "core.py" not in str(sample.fields)


def test_finalize_degrades_item_with_contract_warnings():
    from benchmark_forge import BenchmarkOrchestrator, UserGoal
    from benchmark_forge.domain import BenchmarkItem, ItemStatus, SourceMode, SourceRef

    orchestrator = BenchmarkOrchestrator()
    benchmark = orchestrator.create(UserGoal(goal_id="warning", description="x", target_size=1))
    benchmark.items.append(BenchmarkItem(
        item_id="i", dimension_id="d", source_mode=SourceMode.EXISTING_DATASET,
        source_id="s", question="q", answer="a", options=["a", "b"],
        source_refs=[SourceRef(source_mode=SourceMode.EXISTING_DATASET, source_id="s", sample_id="1")],
        status=ItemStatus.ACCEPTED_WITH_WARNINGS, warnings=["runtime not verified"],
    ))
    orchestrator.finalize(benchmark)
    assert benchmark.status.value == "degraded"



def test_generation_service_creates_dynamic_rag_blueprint_provider(tmp_path):
    from benchmark_forge import BenchmarkGenerationService
    from benchmark_forge.agents import DeterministicMVPAgents
    from benchmark_forge.octagon import OctagonKnowledgeBase
    from benchmark_forge.domain import SourceMode

    kb = OctagonKnowledgeBase(tmp_path / "kb.db")
    service = BenchmarkGenerationService(agents=DeterministicMVPAgents(), knowledge_base=kb)
    result = service.generate("评测 agent 自主拆解和指派 subagent", target_size=1)
    assert result.groundings[0].source_mode == SourceMode.GENERATED_ENVIRONMENT
    assert result.groundings[0].source_id == "rag-environment-blueprint"
    assert result.items[0].source_refs[0].sample_id == "blueprint-0"


def test_generated_environment_deferred_quota_becomes_realization_quota():
    from benchmark_forge import BenchmarkOrchestrator, UserGoal
    from benchmark_forge.actions import AllocationDecision, DesignAction
    from benchmark_forge.domain import Allocation, SourceMode
    from benchmark_forge.reducers import apply_allocation, apply_design

    benchmark = BenchmarkOrchestrator().create(UserGoal(goal_id="g", description="x", target_size=1))
    apply_design(benchmark, DesignAction(action="add_dimension", dimension={"id":"d","name":"D","description":"x"}))
    apply_allocation(benchmark, AllocationDecision(
        action="finish_allocation", shortfall=1,
        allocations=[Allocation(dimension_id="d", source_mode=SourceMode.GENERATED_ENVIRONMENT, source_id="rag-environment-blueprint", deferred_quota=1)],
    ))
    assert benchmark.allocations[0].realization_quota == 1
    assert benchmark.allocations[0].deferred_quota == 0
    assert not any("shortfall" in warning for warning in benchmark.warnings)


def test_generated_environment_multi_dimension_allocation_is_one_blueprint():
    from benchmark_forge import BenchmarkOrchestrator, UserGoal
    from benchmark_forge.actions import AllocationDecision, DesignAction
    from benchmark_forge.domain import Allocation, SourceMode
    from benchmark_forge.reducers import apply_allocation, apply_design

    b = BenchmarkOrchestrator().create(UserGoal(goal_id="g", description="x", target_size=1))
    for dim in ("d1", "d2"):
        apply_design(b, DesignAction(action="add_dimension", dimension={"id":dim,"name":dim,"description":"x"}))
    apply_allocation(b, AllocationDecision(action="finish_allocation", allocations=[
        Allocation(dimension_id="d1", source_mode=SourceMode.GENERATED_ENVIRONMENT, source_id="blueprint", deferred_quota=1),
        Allocation(dimension_id="d2", source_mode=SourceMode.GENERATED_ENVIRONMENT, source_id="blueprint", deferred_quota=1),
    ], shortfall=1))
    assert len(b.allocations) == 1
    assert b.allocations[0].realization_quota == 1
    assert not any("overflow" in w or "shortfall" in w for w in b.warnings)


def test_environment_id_is_normalized_to_octagon_directory_slug():
    from benchmark_forge.domain import EnvironmentContract, ToolContract

    env = EnvironmentContract(
        environment_id="generated.launch_readiness_decomposition.v1",
        tools=[ToolContract(name="x")],
    )
    assert env.environment_id == "generated-launch-readiness-decomposition-v1"
    assert "." not in env.environment_id and "_" not in env.environment_id


def test_preference_alignment_runtime_fallback_abstains_without_evidence():
    from benchmark_forge.preference_alignment import (
        PreferenceAlignmentService, PreferenceEvidenceQuery,
    )

    service = PreferenceAlignmentService()
    evidence = service.retrieve(PreferenceEvidenceQuery(
        context_key="subagent-coordination",
        subject_type="benchmark-plan",
    ))
    decision = service.abstain(evidence, reason="Registry evidence unavailable")
    assert decision.control_action == "abstain"
    assert decision.selected_plan_id is None
    assert decision.offline_alignment_only is True
    assert "human" in " ".join(decision.warnings)


def test_preference_alignment_decision_contract_rejects_runtime_human_action():
    from pydantic import ValidationError
    from benchmark_forge.preference_alignment import PreferenceAlignmentDecision

    with pytest.raises(ValidationError):
        PreferenceAlignmentDecision.model_validate({
            "control_action": "request_human",
            "confidence": 0,
            "evidence_context_ref": "e",
            "rationale": "ask a human",
        })


def test_preference_alignment_agent_uses_typed_output_and_never_selects_without_approved_evidence():
    from pydantic_ai.models.test import TestModel
    from benchmark_forge.preference_alignment import (
        BenchmarkPlanCandidate, PreferenceAlignmentAgent, PreferenceEvidenceContext,
        PreferenceEvidenceQuery,
    )

    agent = PreferenceAlignmentAgent(TestModel(custom_output_args={
        "control_action": "abstain",
        "confidence": 0.0,
        "evidence_context_ref": "retrieval:test-v1",
        "evidence_retrieval_version": "test-v1",
        "rationale": "No approved evidence",
        "criterion_predictions": [],
        "plan_assessments": [],
        "warnings": ["insufficient evidence"],
    }))
    evidence = PreferenceEvidenceContext(query=PreferenceEvidenceQuery(
        context_key="c", subject_type="benchmark-plan",
    ))
    decision = agent.decide(
        goal="Evaluate subagent coordination",
        plan_a=BenchmarkPlanCandidate(plan_id="a", capability="coordination", task_description="A"),
        plan_b=BenchmarkPlanCandidate(plan_id="b", capability="coordination", task_description="B"),
        evidence=evidence,
    )
    assert decision.control_action == "abstain"
    assert decision.offline_alignment_only is True


def test_preference_alignment_select_requires_approved_non_stale_evidence():
    from benchmark_forge.preference_alignment import (
        BenchmarkPlanCandidate, PreferenceAlignmentAgent, PreferenceEvidenceContext,
        PreferenceEvidenceQuery,
    )
    from pydantic_ai.models.test import TestModel

    agent = PreferenceAlignmentAgent(TestModel(custom_output_args={
        "control_action": "select",
        "selected_plan_id": "a",
        "confidence": 0.9,
        "evidence_context_ref": "retrieval:test-v1",
        "evidence_retrieval_version": "test-v1",
        "rationale": "historical evidence",
        "criterion_predictions": [],
        "plan_assessments": [],
        "warnings": [],
    }))
    evidence = PreferenceEvidenceContext(query=PreferenceEvidenceQuery(
        context_key="c", subject_type="benchmark-plan",
    ))
    with pytest.raises(ValueError, match="approved preference evidence"):
        agent.decide(
            goal="g",
            plan_a=BenchmarkPlanCandidate(plan_id="a", capability="c", task_description="A"),
            plan_b=BenchmarkPlanCandidate(plan_id="b", capability="c", task_description="B"),
            evidence=evidence,
        )


def _plan(plan_id: str, *, behavior: str, task: str = "open task"):
    from benchmark_forge import BenchmarkPlanCandidate
    return BenchmarkPlanCandidate(
        plan_id=plan_id,
        capability="subagent-coordination",
        task_description=task,
        behavior_requirements=[behavior],
        environment_description="isolated tool environment",
        artifact_requirements=["report.md"],
        scoring_intent=["trajectory evidence", "artifact evidence"],
    )


def test_double_planning_uses_same_prompt_and_resamples_without_prompt_mutation():
    from benchmark_forge import DoublePlanningService

    calls: list[str] = []
    outputs = iter([
        _plan("a", behavior="delegate independent subtasks"),
        _plan("b-1", behavior="delegate independent subtasks"),
        _plan("b-2", behavior="parallel delegation with acceptance repair", task="open tool task with dependency DAG"),
    ])

    def generator(prompt: str):
        calls.append(prompt)
        return next(outputs)

    pair = DoublePlanningService(
        generator=generator,
        model_id="test-model",
        knowledge_snapshot="kb-v1",
        similarity_threshold=0.8,
        max_resamples=2,
    ).generate_pair("Design an executable subagent coordination benchmark", pair_id="pair-1")
    assert calls == [calls[0], calls[0], calls[0]]
    assert pair.resample_count == 1
    assert pair.status == "ready"
    assert pair.provenance_b.generation_index == 2
    assert pair.provenance_a.prompt_checksum == pair.provenance_b.prompt_checksum == pair.prompt_checksum
    assert pair.provenance_a.model_id == pair.provenance_b.model_id == "test-model"


def test_double_planning_marks_insufficient_diversity_after_bounded_resampling():
    from benchmark_forge import DoublePlanningService

    calls: list[str] = []
    def generator(prompt: str):
        calls.append(prompt)
        return _plan(f"p-{len(calls)}", behavior="same behavior")

    pair = DoublePlanningService(
        generator=generator,
        model_id="m",
        knowledge_snapshot="k",
        similarity_threshold=0.5,
        max_resamples=2,
    ).generate_pair("same prompt")
    assert pair.status == "insufficient_diversity"
    assert pair.resample_count == 2
    assert len(calls) == 4


def test_materialization_gate_only_allows_selected_plan():
    from benchmark_forge import (
        BenchmarkPlanPair, MaterializationGate, PlanProvenance,
        PreferenceAlignmentDecision, prompt_checksum,
    )

    a, b = _plan("a", behavior="a"), _plan("b", behavior="b")
    checksum = prompt_checksum("p")
    pair = BenchmarkPlanPair(
        pair_id="pair", prompt_checksum=checksum, model_id="m", knowledge_snapshot="k",
        plan_a=a, plan_b=b,
        provenance_a=PlanProvenance(prompt_checksum=checksum, model_id="m", knowledge_snapshot="k", branch="a", generation_index=1),
        provenance_b=PlanProvenance(prompt_checksum=checksum, model_id="m", knowledge_snapshot="k", branch="b", generation_index=1),
        similarity_score=0.1,
    )
    decision = PreferenceAlignmentDecision(
        control_action="select", selected_plan_id="b", confidence=0.8,
        evidence_context_ref="e", rationale="approved evidence",
    )
    assert MaterializationGate.authorize(pair, decision).plan_id == "b"

    blocked = decision.model_copy(update={"control_action": "abstain", "selected_plan_id": None})
    with pytest.raises(Exception, match="materialization blocked"):
        MaterializationGate.authorize(pair, blocked)


def test_planning_alignment_pipeline_abstains_without_runtime_human_path():
    from benchmark_forge import DoublePlanningService, PlanningAlignmentPipeline

    outputs = iter([
        _plan("a", behavior="delegate"),
        _plan("b", behavior="repair acceptance", task="open tool task"),
    ])
    pipeline = PlanningAlignmentPipeline(
        planner=DoublePlanningService(
            generator=lambda prompt: next(outputs),
            model_id="m",
            knowledge_snapshot="k",
            similarity_threshold=0.95,
        ),
        evidence_client=None,
        decider=None,
    )
    result = pipeline.run(
        goal="coordinate subagents",
        prompt="Design a benchmark",
        context_key="subagent-coordination",
        subject_type="benchmark-plan",
    )
    assert result.decision.control_action == "abstain"
    assert result.selected_plan is None
    assert result.pair.status == "ready"


def test_planning_alignment_pipeline_regenerates_after_insufficient_diversity():
    from benchmark_forge import DoublePlanningService, PlanningAlignmentPipeline

    pipeline = PlanningAlignmentPipeline(
        planner=DoublePlanningService(
            generator=(lambda outputs: (lambda prompt: next(outputs)))(iter([
                _plan("a", behavior="same"),
                _plan("b-1", behavior="same"),
                _plan("b-2", behavior="same"),
            ])),
            model_id="m",
            knowledge_snapshot="k",
            similarity_threshold=0.5,
            max_resamples=1,
        ),
        evidence_client=None,
        decider=None,
    )
    result = pipeline.run(
        goal="g", prompt="same", context_key="c", subject_type="benchmark-plan",
    )
    assert result.pair.status == "insufficient_diversity"
    assert result.decision.control_action == "regenerate"
    assert result.selected_plan is None


def test_generation_service_alignment_selects_one_branch_before_optional_materialization():
    from benchmark_forge import (
        BenchmarkGenerationService, PreferenceAlignmentDecision,
        PreferenceEvidenceContext, PreferenceEvidenceQuery,
    )
    from benchmark_forge.agents import DeterministicMVPAgents

    class Evidence:
        def search_evidence(self, query):
            return PreferenceEvidenceContext(
                query=query,
                reviewed_summaries=[{"summary_id": "summary-1", "version": 1}],
                retrieval_version="summary-v1",
                coverage={"record_count": 3},
            )

    class Decider:
        def decide(self, *, goal, plan_a, plan_b, evidence):
            return PreferenceAlignmentDecision(
                control_action="select",
                selected_plan_id=plan_a.plan_id,
                confidence=0.8,
                evidence_context_ref="summary-1",
                evidence_retrieval_version=evidence.retrieval_version,
                rationale="approved offline preference evidence",
            )

    service = BenchmarkGenerationService(agents=DeterministicMVPAgents())
    result = service.generate_with_alignment(
        "Design an executable subagent coordination benchmark",
        target_size=1,
        providers=[ProceduralProvider(capacity_hint=1)],
        benchmark_id="aligned",
        evidence_client=Evidence(),
        decider=Decider(),
        similarity_threshold=1.0,
        max_resamples=0,
    )
    assert result.benchmark is not None
    assert result.alignment.decision.control_action == "select"
    assert result.alignment.selected_plan is not None
    assert result.benchmark.manifest["preference_alignment"]["selected_plan_id"] == result.alignment.selected_plan.plan_id
    assert any(event.event_type == "preference_alignment_decided" for event in result.benchmark.events)


def test_generation_service_alignment_does_not_materialize_when_abstaining():
    from benchmark_forge import BenchmarkGenerationService
    from benchmark_forge.agents import DeterministicMVPAgents

    result = BenchmarkGenerationService(agents=DeterministicMVPAgents()).generate_with_alignment(
        "Design an executable benchmark",
        target_size=1,
        providers=[ProceduralProvider(capacity_hint=1)],
        benchmark_id="abstained",
        evidence_client=None,
        decider=None,
        similarity_threshold=1.0,
        max_resamples=0,
    )
    assert result.benchmark is None
    assert result.alignment.decision.control_action == "abstain"
    assert result.alignment.selected_plan is None


def test_default_agent_capacity_library_contains_requested_capacity_set():
    from benchmark_forge import CapabilityId, DEFAULT_CAPACITY_LIBRARY

    expected = {
        "instruction_following",
        "aesthetic_quality",
        "self_tool_building",
        "reflection",
        "hallucination_control",
        "long_horizon_durability",
        "robustness_fault_tolerance",
        "efficiency",
        "context_compression_fidelity",
        "memory_selection_accuracy",
        "autonomous_termination_self_evaluation",
        "delegation_quality",
    }
    actual = {definition.capability_id.value for definition in DEFAULT_CAPACITY_LIBRARY.definitions}
    assert actual == expected
    assert len(DEFAULT_CAPACITY_LIBRARY.definitions) == 12
    assert DEFAULT_CAPACITY_LIBRARY.get(CapabilityId.DELEGATION_QUALITY).human_preference_relevance == "high"


def test_capacity_spec_produces_open_executable_plan_intent():
    from benchmark_forge import CapabilityId, DEFAULT_CAPACITY_LIBRARY

    spec = DEFAULT_CAPACITY_LIBRARY.benchmark_spec(CapabilityId.DELEGATION_QUALITY)
    plan = spec.to_plan_candidate(
        plan_id="delegation-plan-a",
        goal="Evaluate an agent's subagent coordination",
    )
    assert spec.plan_only is True
    assert plan.capability == "delegation_quality"
    assert plan.task_form == "executable_task"
    assert plan.artifact_requirements
    assert "tool_trace" in spec.required_observations
    assert "acceptance quality" in plan.scoring_intent
    assert "multiple-choice" in plan.difficulty_intent


def test_capacity_library_rejects_duplicate_ids_and_supports_search():
    import pytest
    from benchmark_forge import AgentCapacityLibrary, DEFAULT_CAPACITY_LIBRARY

    with pytest.raises(ValueError, match="unique"):
        AgentCapacityLibrary(definitions=[
            DEFAULT_CAPACITY_LIBRARY.definitions[0],
            DEFAULT_CAPACITY_LIBRARY.definitions[0],
        ])
    results = DEFAULT_CAPACITY_LIBRARY.search("委派 subagent")
    assert any(item.capability_id.value == "delegation_quality" for item in results)
