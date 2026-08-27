from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .actions import AllocationDecision, DesignAction, ExecutorResult, GroundingAction, VerificationResult
from .domain import (
    Allocation,
    Benchmark,
    BenchmarkDimension,
    BenchmarkGrounding,
    BenchmarkItem,
    GroundingScores,
    GroundingStatus,
    ItemStatus,
    SourceMode,
    SourceRef,
    TransformPlan,
)
from .providers import SourceProvider, SourceSample


class RoleAgents(Protocol):
    """Contract for the original five roles.

    A PydanticAI implementation can satisfy this protocol by returning these
    models from ``Agent.run``. The MVP ships with deterministic agents so the
    graph and artifact contract can be tested without an API key.
    """

    def design(self, benchmark: Benchmark) -> list[DesignAction]: ...
    def ground(self, benchmark: Benchmark, providers: list[SourceProvider]) -> list[GroundingAction]: ...
    def allocate(self, benchmark: Benchmark) -> AllocationDecision: ...
    def execute(self, benchmark: Benchmark, allocation: Allocation, provider: SourceProvider, sample: SourceSample) -> ExecutorResult: ...
    def verify(self, benchmark: Benchmark, item: BenchmarkItem) -> VerificationResult: ...


@dataclass
class DeterministicMVPAgents:
    """A no-network reference implementation for the MVP graph.

    It intentionally produces simple items. Its purpose is to validate state,
    source modes, degraded runs, and event/evidence behavior before adding LLM
    prompts and PydanticAI model calls.
    """

    def design(self, benchmark: Benchmark) -> list[DesignAction]:
        if benchmark.dimensions:
            return [DesignAction(action="finish_design", rationale="dimensions already present")]
        return [
            DesignAction(
                action="add_dimension",
                dimension=BenchmarkDimension(
                    id="goal_reasoning",
                    name="Goal reasoning",
                    description=benchmark.user_goal.description,
                    capability="reasoning",
                    task_form="static_question",
                ),
                rationale="MVP fallback dimension derived from the user goal",
            ),
            DesignAction(action="finish_design", rationale="minimal MVP design complete"),
        ]

    def ground(self, benchmark: Benchmark, providers: list[SourceProvider]) -> list[GroundingAction]:
        actions: list[GroundingAction] = []
        for dimension in benchmark.dimensions:
            available = [p for p in providers if p.capacity() > 0]
            if not available:
                grounding = BenchmarkGrounding(
                    dimension_id=dimension.id,
                    source_mode=SourceMode.BLOCKED,
                    status=GroundingStatus.BLOCKED,
                    reasons={"resource": "no source provider has available samples"},
                    evidence={"providers_checked": [getattr(p, "provider_id", "unknown") for p in providers]},
                )
                actions.append(GroundingAction(action="block_grounding", grounding=grounding,
                                               rationale="preserve dimension while no source is available"))
                continue
            for chosen in available:
                mode = getattr(chosen, "source_mode", SourceMode.EXISTING_DATASET)
                status = GroundingStatus.READY if mode == SourceMode.EXISTING_DATASET else GroundingStatus.PROVISIONAL
                grounding = BenchmarkGrounding(
                    dimension_id=dimension.id,
                    source_mode=mode,
                    source_id=chosen.provider_id,
                    plan=TransformPlan(rationale="deterministic MVP source realization"),
                    estimated_capacity=chosen.capacity(),
                    executable_capacity=chosen.capacity(),
                    status=status,
                    scores=GroundingScores(
                        alignment=3.0 if mode == SourceMode.EXISTING_DATASET else 2.0,
                        robustness=3.0,
                        signal_preservation=3.0,
                        answerability=3.0,
                        uniqueness=2.0,
                    ),
                    reasons={"mode": mode.value, "quality": "MVP provisional grounding"},
                    evidence={"inspection": chosen.inspect()},
                )
                actions.append(GroundingAction(action="add_grounding", grounding=grounding,
                                               rationale="candidate source for allocation and replenishment"))
        actions.append(GroundingAction(action="finish_grounding", rationale="grounding pass complete"))
        return actions

    def allocate(self, benchmark: Benchmark) -> AllocationDecision:
        target = benchmark.user_goal.target_size
        groundings = [g for g in benchmark.groundings if g.executable]
        if not groundings:
            return AllocationDecision(action="finish_allocation", shortfall=target,
                                      rationale="no executable grounding")
        remaining = target
        allocations: list[Allocation] = []
        for grounding in groundings:
            if remaining <= 0:
                break
            quota = min(remaining, grounding.executable_capacity)
            allocations.append(Allocation(
                dimension_id=grounding.dimension_id,
                source_mode=grounding.source_mode,
                source_id=grounding.source_id,
                executable_quota=quota,
                replenishment_strategy="try next provider" if quota < remaining else None,
            ))
            remaining -= quota
        return AllocationDecision(
            action="set_allocation",
            allocations=allocations,
            shortfall=remaining,
            rationale="best-effort executable allocation",
        )

    def execute(self, benchmark: Benchmark, allocation: Allocation,
                provider: SourceProvider, sample: SourceSample) -> ExecutorResult:
        fields = sample.fields
        context = str(fields.get("context") or fields.get("text") or fields.get("facts") or fields)
        answer = str(fields.get("answer") or fields.get("label") or "supported by source")
        question = str(fields.get("question") or f"What is supported by this source about {fields.get('subject', 'the subject')}?")
        options = [answer, "Not supported by the source", "The opposite of the source claim"]
        item = BenchmarkItem(
            item_id=f"{benchmark.benchmark_id}:{allocation.dimension_id}:{sample.sample_id}",
            dimension_id=allocation.dimension_id,
            source_mode=allocation.source_mode,
            source_id=provider.provider_id,
            question=question,
            context=context,
            options=options,
            answer=answer,
            source_refs=[SourceRef(source_mode=allocation.source_mode, source_id=provider.provider_id,
                                   sample_id=sample.sample_id, fields=sorted(fields))],
            generation_log=["deterministic_mvp_executor"],
        )
        return ExecutorResult(action="item", item=item, source_id=provider.provider_id, sample_id=sample.sample_id)

    def verify(self, benchmark: Benchmark, item: BenchmarkItem) -> VerificationResult:
        reasons: dict[str, str] = {}
        warnings: list[str] = []
        if item.item_kind == "executable_task":
            task = item.executable_task
            if task is None:
                return VerificationResult(valid=False, status="failed", reasons={"schema": "missing executable contract"}, control_action="discard")
            if not task.instruction.strip() or not task.scoring.dimensions:
                return VerificationResult(valid=False, status="failed", reasons={"execution": "instruction/scoring missing"}, control_action="discard")
            if task.environment.maturity != "existing":
                warnings.append(f"environment_maturity={task.environment.maturity}")
        else:
            if not (item.question or "").strip():
                return VerificationResult(valid=False, status="failed", reasons={"schema": "empty question"}, control_action="discard")
            if not (item.answer or "").strip():
                return VerificationResult(valid=False, status="failed", reasons={"schema": "empty answer"}, control_action="discard")
        if not item.source_refs:
            return VerificationResult(valid=False, status="failed", reasons={"source": "missing source refs"}, control_action="discard")
        if item.source_mode != SourceMode.EXISTING_DATASET:
            warnings.append(f"source_mode={item.source_mode.value}")
        if len(set(item.options or [])) != len(item.options or []):
            warnings.append("duplicate options")
        if warnings:
            return VerificationResult(valid=True, status="accepted_with_warnings", reasons=reasons,
                                      warnings=warnings, evidence={"mvp": True})
        return VerificationResult(valid=True, status="verified", reasons={"schema": "ok", "source": "ok"},
                                  evidence={"mvp": True})
