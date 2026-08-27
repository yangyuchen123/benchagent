"""Model-backed implementations of the original five roles.

This module is intentionally thin: PydanticAI owns model calls and output
validation; ``BenchmarkOrchestrator`` and reducers own state transitions.
Tools can be added later without changing the role contracts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .actions import AllocationDecision, DesignAction, ExecutorResult, GroundingAction, VerificationResult
from .domain import (
    Allocation, Benchmark, BenchmarkItem, ExecutableTaskContract, SourceRef,
    normalize_contract_bindings,
)
from .providers import SourceProvider, SourceSample
from .pydantic_ai_adapter import PydanticAIRunner
from .octagon import EnvironmentCatalog, OctagonKnowledgeBase
from .staging import EnvironmentScaffoldBundle
from .scorer_design import ScorerDesign, ScorerReview
from .rubric_review import RubricIntegrityReview, review_rubric_integrity, revise_rubric_integrity
from .component_agents import generate_component_output, generate_component_outputs, repair_component_output
from .environment_ir import EnvironmentIR, IRComponentOutput, link_component_outputs
from .materialization_workflow import FailureObservation, RepairPlan


def _dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in value]
    return json.dumps(value, ensure_ascii=False, indent=2)


@dataclass
class PydanticAIRoleAgents:
    """PydanticAI-backed implementation of the existing five role contract."""

    model: Any
    # Per-call model timeout. Formal runs should fail fast enough to leave
    # telemetry and a resumable artifact instead of hanging indefinitely.
    llm_timeout: float = 90.0
    environment_catalog: EnvironmentCatalog | None = None
    knowledge_base: OctagonKnowledgeBase | None = None
    design_instructions: str = (
        "You are the Design Agent. The system under evaluation is a complete Agent, not a bare LLM. "
        "It may already own native tools, filesystem/workspace access, memory, multi-turn control and subagent "
        "spawn/message/wait/trace capabilities. Design capability dimensions around what that Agent actually does "
        "with those capabilities. The benchmark environment supplies the task world, controlled data, faults, "
        "state and independent verification; it must not reimplement the target Agent capability as a fake tool. "
        "For example, a delegation benchmark must observe real host-Agent subagent calls and child traces, not "
        "provide a synthetic subagent_spawn tool that merely returns fabricated child status. If decomposition itself "
        "is the target, do not prescribe a hidden gold DAG/CoordinationContract; score properties of the Agent's native "
        "trajectory instead. Design capability "
        "dimensions, not quiz topics or tool-argument formatting tests. A real agent benchmark is normally an open "
        "executable task with observable state, artifacts and trajectory evidence. Set task_form=executable_task "
        "unless the user explicitly requests a static knowledge test. multiple_choice is not an acceptable substitute "
        "for Agent behavior. For subagent goals explicitly separate decomposition, assignment, coordination, "
        "acceptance/repair and final correctness dimensions. Return only DesignAction objects; do not create final tasks."
    )
    grounding_instructions: str = (
        "You are the Grounding Agent. The evaluated subject is a complete Agent, not a bare LLM. Determine what "
        "resources can honestly realize each dimension without replacing the Agent's native capability with a "
        "benchmark-side simulation. Native subagent, workspace, memory or multi-turn behavior must be available "
        "from and observed on the host Agent; environment resources may supply controlled task data, faults, state "
        "and independent verifiers. Distinguish raw datasets from existing executable environments. An Octagon environment profile/task "
        "can ground an executable task through protocol octagon.env.v1; a text row alone cannot prove tools, "
        "state, artifacts or a scorer exist. For data-dependent generated environments, distinguish Contract design "
        "capacity from materialization readiness: require a concrete material, runtime generator, or evaluation-injection "
        "binding; otherwise mark the grounding partial/pending rather than ready. grounding.source_id MUST equal a top-level Resource catalog provider_id; "
        "put environment_id inside grounding evidence rather than using it as source_id. For generated_environment providers, "
        "set realization_capacity from provider capacity even when runtime status is pending. Use partial/pending when an environment contract can be designed "
        "but is not materialized. Never invent a source, tool, scorer or hidden expected result."
    )
    allocation_instructions: str = (
        "You are the Allocation Agent. Allocate executable quota only to groundings with enough environment "
        "and task capacity. Keep unsupported executable tasks deferred rather than converting them into quizzes. "
        "Use realization_quota for generated_environment blueprints that Benchmark Forge can turn into contracts now; "
        "use executable_quota only for already materialized environments, and deferred_quota only when neither action is possible. "
        "Best effort and explicit shortfall are valid. Return one AllocationDecision."
    )
    executor_instructions: str = (
        "You are the Executor / Sample Realization Agent. The evaluated subject is a complete Agent, not a prompt-only LLM. "
        "Realize one allocation without replacing the target Agent capability with a benchmark-side simulator. For agent "
        "behavior goals, produce item_kind=executable_task with an ExecutableTaskContract modeled after octagon.env.v1: "
        "an open instruction, environment reference, tools/entrypoints/materials, workspace policy, required artifacts, "
        "a concrete public schema_def/schema_path for every JSON artifact described as structured or schema-valid, "
        "weighted scoring dimensions and observable evidence. Put native subagent/workspace/memory/multi-turn requirements "
        "in agent_capabilities and do not implement them as synthetic benchmark tools. When subagents are involved, add agent capability "
        "requirements and a CoordinationContract describing the subtask DAG, context, write scopes, output contracts "
        "and acceptance checks. A single integrated executable task may cover multiple benchmark dimensions; record them in covered_dimension_ids. Do not include a gold answer or options for an "
        "executable task. Use static_question only for genuinely static knowledge evaluation. Preserve source refs, "
        "do not expose private/scorer/expected data, and do not mark the item verified."
    )
    verification_instructions: str = (
        "You are Verification & Control. For executable tasks verify that the instruction is open, the environment "
        "has tools/entrypoints/materials, artifacts or state are observable, every structured/schema-valid JSON artifact "
        "has one concrete typed schema binding shared by task/runtime/scorer/tests, scoring dimensions name evidence "
        "sources, workspace/private boundaries are explicit, and eval-system could materialize the references. "
        "For subagent tasks, topology alone is insufficient: verify assignment payload quality, dependency timing, "
        "post-child validation and targeted repair evidence. Do not require a hidden fixed DAG when decomposition is "
        "the capability under test. Reject a benchmark that reimplements an Agent-native "
        "capability as a generated simulator: synthetic subagent_spawn/message/wait outputs do not measure real "
        "delegation, and a hand-written memory/workspace wrapper must not be presented as evidence of native Agent "
        "memory/workspace ability. Controlled environment data/fault/gate tools are valid only when they are experimental "
        "apparatus rather than the target capability itself. Reject any executable behavior benchmark compressed into multiple choice. Existing environments may be "
        "accepted; generated contracts without implementation should be accepted only with warnings or deferred. "
        "Also check source traceability and leakage. Treat missing required scenario material/generator/injection "
        "bindings, contradictory public field counts, or data-dependent tasks with an empty scenario contract as "
        "blocking errors rather than warnings. At the pre-materialization Contract stage, a typed generated MaterialContract "
        "or scorer_ref with a stable ref is allowed to have no checksum/version yet: component materialization is responsible "
        "for producing and validating it. Do not reject a generated Contract merely because those generated files do not exist "
        "before materialization; reject only missing/untyped/unresolvable bindings. A required case tag such as late_injection "
        "may be implemented inside a bundled fixture and does not require a dynamic injection tool unless the Contract explicitly "
        "requires temporal host injection. Return one VerificationResult."
    )

    def design(self, benchmark: Benchmark) -> list[DesignAction]:
        runner = PydanticAIRunner(model=self.model, output_type=list[DesignAction], instructions=self.design_instructions, timeout=self.llm_timeout, label="role.design")
        catalog_context = self.environment_catalog.agent_context(limit=20) if self.environment_catalog else None
        retrieved_context = self.knowledge_base.context(benchmark.user_goal.description, role="design", source_kinds=["environment_profile", "task_spec", "documentation"]) if self.knowledge_base else None
        prompt = (
            f"User goal:\n{_dump(benchmark.user_goal)}\nCurrent benchmark:\n{_dump(benchmark)}"
            f"\nExisting executable benchmark catalog (precedent only):\n{_dump(catalog_context)}"
            f"\nRetrieved benchmark knowledge:\n{_dump(retrieved_context)}"
        )
        return runner.run_sync(prompt)

    def ground(self, benchmark: Benchmark, providers: list[SourceProvider]) -> list[GroundingAction]:
        runner = PydanticAIRunner(model=self.model, output_type=list[GroundingAction], instructions=self.grounding_instructions, timeout=self.llm_timeout, label="role.grounding")
        catalog = [p.inspect() | {"source_mode": getattr(p, "source_mode", "unknown")} for p in providers]
        environment_context = self.environment_catalog.agent_context(limit=20) if self.environment_catalog else None
        retrieved_context = self.knowledge_base.context(benchmark.user_goal.description, role="grounding", source_kinds=["environment_profile", "task_spec", "documentation", "input"]) if self.knowledge_base else None
        prompt = (
            f"Benchmark:\n{_dump(benchmark)}\nResource catalog:\n{_dump(catalog)}"
            f"\nExisting environment benchmark context:\n{_dump(environment_context)}"
            f"\nRetrieved environment knowledge:\n{_dump(retrieved_context)}"
        )
        return runner.run_sync(prompt)

    def allocate(self, benchmark: Benchmark) -> AllocationDecision:
        runner = PydanticAIRunner(model=self.model, output_type=AllocationDecision, instructions=self.allocation_instructions, timeout=self.llm_timeout, label="role.allocation")
        retrieved_context = self.knowledge_base.context(benchmark.user_goal.description, role="allocation", source_kinds=["environment_profile", "task_spec", "documentation"]) if self.knowledge_base else None
        prompt = f"User goal:\n{_dump(benchmark.user_goal)}\nGroundings:\n{_dump(benchmark.groundings)}\nRetrieved knowledge:\n{_dump(retrieved_context)}"
        return runner.run_sync(prompt)

    def execute(self, benchmark: Benchmark, allocation: Allocation,
                provider: SourceProvider, sample: SourceSample) -> ExecutorResult:
        """Realize one executable task with a small contract-first model call.

        The old path asked the model to emit ``ExecutorResult`` and a nested
        ``BenchmarkItem`` at the same time. That made the model responsible for
        transport/action fields, provenance, lifecycle state, and the actual
        task contract in one response. The role boundary is preserved here, but
        the LLM only produces the contract; Forge owns the wrapper fields.
        """
        dimension = next((d for d in benchmark.dimensions if d.id == allocation.dimension_id), None)
        retrieved_context = self.knowledge_base.context(
            f"{allocation.dimension_id} {sample.fields}", role="executor",
            source_kinds=["environment_profile", "task_spec", "documentation", "input", "material"],
            limit=3, max_chars=3_500,
        ) if self.knowledge_base else None
        instructions = (
            "You are the Executor's contract designer. Return ONLY one "
            "ExecutableTaskContract. Do not return ExecutorResult, BenchmarkItem, "
            "Markdown, explanations, private answers, or scorer implementation. "
            "Design one open executable benchmark for a complete Agent, not a prompt-only LLM. Keep it concise. "
            "Assume the evaluated Agent uses its declared native capabilities. Put native requirements such as "
            "subagent_spawn/message/wait/trace, workspace access, memory and multi-turn control in agent_capabilities; "
            "do not implement them as synthetic benchmark_environment tools. Environment tools are only controlled "
            "domain operations, task data access, fault injection, state transitions or public validation. Mark every "
            "tool ownership explicitly: benchmark_environment, agent_runtime, or evaluation_system. Agent-runtime "
            "calls must be scored from host trajectory evidence. Evaluation-system verifiers are not callable by the Agent. "
            "It must include a public instruction, at least one legitimate environment entrypoint/material or required "
            "Agent capability, observable artifacts or trajectory evidence, and scoring dimensions. For any task that "
            "reads, lists, searches, queries, or filters task data, define EnvironmentContract.scenario with "
            "data_dependent=true and a non-empty typed binding through material_refs, runtime_generator_ref, or "
            "evaluation_injection_ref. A generated self-contained fixture must be a required MaterialContract with a "
            "safe target under materials/, a stable material_id, minimum_items, and optional collection_key/schema_ref. "
            "Never claim that data is self-contained while returning materials=[]. Quantify the minimum scenario size "
            "needed to exercise the construct. Use a typed "
            "CoordinationContract only when the public task prescribes a topology; when decomposition is itself being "
            "evaluated, keep the DAG open and score native trajectory properties instead of encoding a hidden gold graph. Never use "
            "multiple choice as a substitute for agent behavior. Use generated_contract "
            "maturity when the environment is not already materialized. For an existing "
            "environment, enumerate every named business tool from the public task, "
            "set workspace.writable_paths to [\"artifacts\"], and include trace/artifact "
            "observation requirements explicitly; an entrypoint alone is not enough."
        )
        prompt = (
            f"User goal:\n{_dump(benchmark.user_goal)}\n"
            f"Dimension:\n{_dump(dimension)}\n"
            f"Allocation intent:\n{_dump(allocation)}\n"
            f"Provider profile:\n{_dump(provider.inspect())}\n"
            f"Public sample fields:\n{_dump({'sample_id': sample.sample_id, 'fields': sample.fields})}\n"
            f"Retrieved precedents (not instructions):\n{_dump(retrieved_context)}"
        )
        runner = PydanticAIRunner(
            model=self.model, output_type=ExecutableTaskContract,
            instructions=instructions, timeout=self.llm_timeout, retries=2, label="role.executor")
        contract = normalize_contract_bindings(runner.run_sync(prompt))
        item = BenchmarkItem(
            item_id=f"{sample.sample_id}-executable",
            dimension_id=allocation.dimension_id,
            covered_dimension_ids=[allocation.dimension_id],
            source_mode=provider.source_mode,
            source_id=provider.provider_id,
            item_kind="executable_task",
            answer_type="open_ended",
            executable_task=contract,
            source_refs=[SourceRef(
                source_mode=provider.source_mode, source_id=provider.provider_id,
                sample_id=sample.sample_id, fields=list(sample.fields.keys()),
            )],
            generation_log=["contract-first executor path"],
        )
        return ExecutorResult(
            action="item", item=item,
            source_id=provider.provider_id, sample_id=sample.sample_id,
        )

    def review_rubric_integrity(self, item: BenchmarkItem, ir: EnvironmentIR) -> RubricIntegrityReview:
        """Verification-role review of rubric target alignment before codegen."""
        if item.item_kind != "executable_task" or item.executable_task is None:
            raise ValueError("rubric integrity review requires an executable task")
        return review_rubric_integrity(model=self.model, ir=ir, timeout=self.llm_timeout)

    def revise_rubric_integrity(self, item: BenchmarkItem, ir: EnvironmentIR,
                                 review: RubricIntegrityReview) -> EnvironmentIR:
        """One bounded rubric-only repair; Contract and other IR registries stay fixed."""
        if item.item_kind != "executable_task" or item.executable_task is None:
            raise ValueError("rubric revision requires an executable task")
        return revise_rubric_integrity(model=self.model, ir=ir, review=review, timeout=self.llm_timeout)

    def design_scorer(self, item: BenchmarkItem) -> ScorerDesign:
        """Verification & Control designs scorer semantics before Executor writes code."""
        if item.item_kind != "executable_task" or item.executable_task is None:
            raise ValueError("scorer design requires an executable task")
        instructions = (
            "You are the existing Verification & Control role, designing the semantic scoring strategy before "
            "the Executor implements scorer.py. Treat scoring as construct measurement, not string matching. "
            "For every scoring dimension propose at least two genuinely different implementation options, such "
            "as canonical runtime records, correlated native events, environment state, observed artifacts, or "
            "an independent deterministic verifier. Rank authoritative runtime evidence above Agent self-report. "
            "Define workspace-resolution alternatives and graceful evidence fallback. Every scored condition must "
            "be publicly satisfiable from the task or be a property observable without requiring hidden identifiers. "
            "Include calibration cases for strong execution, weak execution, fabricated self-report, missing wire "
            "capture, and missing artifacts. Return ScorerDesign only; do not write Python code."
        )
        runner = PydanticAIRunner(model=self.model, output_type=ScorerDesign, instructions=instructions, timeout=self.llm_timeout, label="role.scorer_design")
        retrieved = self.knowledge_base.context(
            item.executable_task.instruction, role="verification",
            source_kinds=["environment_profile", "task_spec", "documentation"], limit=6, max_chars=8_000,
        ) if self.knowledge_base else None
        return runner.run_sync(
            f"Executable benchmark item:\n{_dump(item)}\n"
            f"Retrieved scoring precedents (public patterns only):\n{_dump(retrieved)}"
        )

    def generate_environment_component(
        self, item: BenchmarkItem, ir: EnvironmentIR, component_id: str,
        dependency_outputs: list[IRComponentOutput] | None = None,
        scorer_design: ScorerDesign | None = None,
    ) -> IRComponentOutput:
        """Generate one dependency-bounded component for resumable workflows."""
        if component_id not in {"manifest", "runtime", "scorer", "tests"}:
            raise ValueError(f"unsupported component: {component_id}")
        return generate_component_output(
            model=self.model, component_id=component_id, item=item, ir=ir,
            scorer_design=scorer_design, dependency_outputs=dependency_outputs,
            timeout=self.llm_timeout,
        )

    def diagnose_materialization_failure(
        self, item: BenchmarkItem, ir: EnvironmentIR,
        outputs: list[IRComponentOutput], observation: FailureObservation,
    ) -> RepairPlan:
        """Assign an ambiguous integration failure without changing semantics."""
        instructions = (
            "You are the Materialization Diagnosis Agent. Diagnose one failure below an already accepted Contract "
            "and checksum-frozen EnvironmentIR. You may assign repair only to manifest, runtime, scorer, or tests. "
            "Never propose changing, simplifying, regenerating, or reinterpreting the Contract or IR. Components "
            "are peers connected through the IR interface: do not make one component call another component's "
            "private implementation. For failed tests, decide whether the test made an invalid implementation "
            "assumption or correctly exposed public Runtime/Scorer behavior. Prefer the smallest responsible write "
            "set. Return stop when the evidence is insufficient or the defect is outside component codegen."
        )
        runner = PydanticAIRunner(
            model=self.model, output_type=RepairPlan, instructions=instructions,
            timeout=self.llm_timeout, label="materialization.diagnose",
        )
        return runner.run_sync(
            f"Benchmark item:\n{_dump(item)}\nFrozen IR:\n{_dump(ir)}\n"
            f"Current component outputs:\n{_dump(outputs)}\n"
            f"Failure observation:\n{_dump(observation)}"
        )

    def materialize_environment_components(
        self, item: BenchmarkItem, ir: EnvironmentIR,
        scorer_design: ScorerDesign | None = None,
    ) -> EnvironmentScaffoldBundle:
        """Generate four bounded component outputs and link them through Frozen IR."""
        outputs = generate_component_outputs(
            model=self.model, item=item, ir=ir, scorer_design=scorer_design,
            timeout=self.llm_timeout,
        )
        return link_component_outputs(ir, outputs)

    def repair_environment_component(
        self, item: BenchmarkItem, ir: EnvironmentIR, component_id: str,
        current: IRComponentOutput, review: Any,
        scorer_design: ScorerDesign | None = None,
        dependency_outputs: list[IRComponentOutput] | None = None,
    ) -> IRComponentOutput:
        """Repair one component only; the Forge linker reassembles the bundle."""
        if component_id not in {"manifest", "runtime", "scorer", "tests"}:
            raise ValueError(f"unsupported component: {component_id}")
        return repair_component_output(
            model=self.model, component_id=component_id, item=item, ir=ir,
            current=current, review=review, scorer_design=scorer_design,
            dependency_outputs=dependency_outputs, timeout=self.llm_timeout,
        )

    def materialize_environment(self, item: BenchmarkItem, scorer_design: ScorerDesign | None = None) -> EnvironmentScaffoldBundle:
        """Executor second phase: turn a generated contract into runnable files."""
        if item.item_kind != "executable_task" or item.executable_task is None:
            raise ValueError("materialization requires an executable task")
        instructions = (
            "You are still the Executor role, now performing environment implementation. Convert the supplied "
            "ExecutableTaskContract into a complete self-contained agent-octagon environment file bundle. Return "
            "EnvironmentScaffoldBundle only. Required files are meta.yaml, core.py, scorer.py, at least one "
            "tasks/*.json, README.md and tests/test_environment.py. Generate any public inputs/materials needed by "
            "the task. meta dimensions must exactly match the scoring contract. The task JSON must contain id, "
            "env_name, prompt and timeout_seconds. scorer.py must define score(*, attempt_id, task, env_db=None, "
            "trace=None, final_state=None, **kwargs) and return dimension/value/detail records with numeric values on every failure path. "
            "Target the current AgentOctagon ABI: materials.agent path/target mounts, @octagon.env_api.env_tool registrations, "
            "and an authenticated FastMCP stdio bridge using OCTAGON_ATTEMPT_ID/OCTAGON_ENV_TOKEN/OCTAGON_BASE_URL. core.py may be an "
            "empty documented module when the benchmark evaluates native agent/subagent capabilities. Do not use "
            "host absolute paths, credentials, network services, private reasoning, or unavailable project imports. "
            "Use deterministic synthetic fixtures and observable artifacts/trajectory. Code is untrusted and will "
            "only be executed later by eval-system isolation. Environment IDs and directory names must match "
            "^[a-z0-9]+(?:-[a-z0-9]+)*$: lowercase ASCII letters/digits/hyphens only, with no dots or underscores. "
            "Implement the supplied Verification & Control scorer design when present. Select and combine suitable "
            "implementation options per dimension rather than reducing all evidence to one brittle string check. "
            "The scorer should resolve the attempt workspace through available runtime inputs, apply documented "
            "fallbacks when one evidence channel is unavailable, and never require undisclosed internal IDs."
        )
        runner = PydanticAIRunner(model=self.model, output_type=EnvironmentScaffoldBundle, instructions=instructions, timeout=self.llm_timeout, label="legacy.environment_materialization")
        retrieved = self.knowledge_base.context(
            item.executable_task.instruction, role="environment_materialization",
            source_kinds=["environment_profile", "task_spec", "documentation"], limit=6, max_chars=8_000,
        ) if self.knowledge_base else None
        return runner.run_sync(
            f"Executable benchmark item:\n{_dump(item)}\n"
            f"Verification & Control scorer design:\n{_dump(scorer_design)}\n"
            f"Retrieved implementation precedents (do not copy private answers):\n{_dump(retrieved)}"
        )

    def review_environment_scorer(
        self, item: BenchmarkItem, bundle: EnvironmentScaffoldBundle, scorer_design: ScorerDesign
    ) -> ScorerReview:
        """Verification & Control performs semantic review of implemented scorer.py."""
        instructions = (
            "You are Verification & Control reviewing an implemented benchmark scorer. Compare scorer.py, public "
            "task prompt, ExecutableTaskContract, and ScorerDesign. Do not approve merely because code parses or "
            "returns a score. Check each dimension for construct coverage, public satisfiability, workspace access, "
            "evidence authority, fallback behavior, and resistance to self-reported/fabricated logs. A scorer may use "
            "different implementations than the initial options if they measure the same public construct. Return "
            "pass only when frozen-evidence rescoring would be meaningful; otherwise return concrete repair instructions."
        )
        runner = PydanticAIRunner(model=self.model, output_type=ScorerReview, instructions=instructions, timeout=self.llm_timeout, label="role.scorer_review")
        return runner.run_sync(
            f"Benchmark item:\n{_dump(item)}\nScorer design:\n{_dump(scorer_design)}\n"
            f"Environment bundle:\n{_dump(bundle)}"
        )

    def repair_environment_scorer(
        self, item: BenchmarkItem, bundle: EnvironmentScaffoldBundle, scorer_design: ScorerDesign, review: ScorerReview
    ) -> EnvironmentScaffoldBundle:
        """Executor repairs the implementation in response to Verification & Control."""
        instructions = (
            "You are the existing Executor role repairing an environment implementation after Verification & Control. "
            "Return the complete EnvironmentScaffoldBundle, preserving valid public task/material files and changing "
            "scorer.py plus tests or documentation as needed. Follow the scorer design and every repair instruction. "
            "Use multiple authoritative/fallback evidence paths where appropriate. Do not introduce hidden criteria, "
            "host paths, credentials, or self-report-only scoring."
        )
        runner = PydanticAIRunner(model=self.model, output_type=EnvironmentScaffoldBundle, instructions=instructions, timeout=self.llm_timeout, label="legacy.environment_materialization")
        return runner.run_sync(
            f"Benchmark item:\n{_dump(item)}\nScorer design:\n{_dump(scorer_design)}\n"
            f"Review requiring repair:\n{_dump(review)}\nCurrent bundle:\n{_dump(bundle)}"
        )

    def verify(self, benchmark: Benchmark, item: BenchmarkItem) -> VerificationResult:
        runner = PydanticAIRunner(model=self.model, output_type=VerificationResult, instructions=self.verification_instructions, timeout=self.llm_timeout, label="role.verification")
        retrieved_context = self.knowledge_base.context(benchmark.user_goal.description, role="verification", source_kinds=["environment_profile", "task_spec", "documentation"]) if self.knowledge_base else None
        prompt = f"Benchmark goal:\n{_dump(benchmark.user_goal)}\nItem:\n{_dump(item)}\nRetrieved scoring knowledge:\n{_dump(retrieved_context)}"
        return runner.run_sync(prompt)
