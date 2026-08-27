from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .actions import DesignAction, GroundingAction
from .checkpoint import CheckpointStage, load_checkpoint, save_checkpoint
from .agents import DeterministicMVPAgents, RoleAgents
from .domain import Benchmark, BenchmarkEvent, BenchmarkStatus, ItemStatus, ReplenishmentRequest, UserGoal
from .persistence import save_benchmark
from .providers import SourceProvider
from .reducers import apply_allocation, apply_design, apply_grounding


@dataclass
class RunConfig:
    max_design_rounds: int = 2
    max_grounding_rounds: int = 2
    max_allocation_rounds: int = 2
    max_item_attempts: int = 2
    accept_warnings: bool = True
    seed: int = 0
    model_id: str = "deterministic-mvp"


@dataclass
class BenchmarkOrchestrator:
    agents: RoleAgents = field(default_factory=DeterministicMVPAgents)
    config: RunConfig = field(default_factory=RunConfig)

    def _event(self, benchmark: Benchmark, role: str, event_type: str,
               message: str = "", payload: dict[str, Any] | None = None) -> None:
        benchmark.events.append(BenchmarkEvent(
            event_id=str(uuid4()), role=role, event_type=event_type,
            message=message, payload=payload or {},
        ))

    def create(self, goal: UserGoal, *, benchmark_id: str | None = None) -> Benchmark:
        return Benchmark(
            benchmark_id=benchmark_id or str(uuid4()),
            user_goal=goal,
            manifest={"model_id": self.config.model_id, "seed": self.config.seed},
        )

    def run(self, goal: UserGoal, providers: list[SourceProvider], *,
            benchmark_id: str | None = None, artifact_root: str | None = None,
            resume_from: str | None = None, checkpoint_root: str | None = None) -> Benchmark:
        checkpoint_path = checkpoint_root or artifact_root
        if resume_from:
            checkpoint = load_checkpoint(resume_from)
            benchmark = checkpoint.benchmark
            next_stage: CheckpointStage = checkpoint.next_stage
            self._event(benchmark, "orchestrator", "checkpoint_resumed",
                        payload={"path": resume_from, "next_stage": next_stage})
        else:
            benchmark = self.create(goal, benchmark_id=benchmark_id)
            next_stage = "design"

        stages: list[tuple[CheckpointStage, Any]] = [
            ("design", lambda: self.design(benchmark)),
            ("grounding", lambda: self.ground(benchmark, providers)),
            ("allocation", lambda: self.allocate(benchmark)),
            ("executor", lambda: self.execute(benchmark, providers)),
            ("verification", lambda: self.verify_pending(benchmark)),
            ("replenish", lambda: self.replenish(benchmark, providers)),
            ("finalize", lambda: self.finalize(benchmark)),
        ]
        started = False
        for stage, action in stages:
            if stage == next_stage:
                started = True
            if not started:
                continue
            action()
            next_index = [name for name, _ in stages].index(stage) + 1
            if checkpoint_path and next_index < len(stages):
                following = stages[next_index][0]
                self._event(benchmark, "orchestrator", "checkpoint_written",
                            payload={"next_stage": following})
                save_checkpoint(Path(checkpoint_path) / "checkpoint.json", benchmark, following)

        if artifact_root:
            save_benchmark(artifact_root, benchmark)
        return benchmark

    def design(self, benchmark: Benchmark) -> None:
        for round_no in range(self.config.max_design_rounds):
            actions = self.agents.design(benchmark)
            self._event(benchmark, "design", "agent_output", payload={"round": round_no, "actions": [a.model_dump() for a in actions]})
            for action in actions:
                try:
                    apply_design(benchmark, action)
                except Exception as exc:  # preserve state and expose failure
                    self._event(benchmark, "design", "validation_error", str(exc), {"action": action.model_dump()})
                    benchmark.warnings.append(f"design: {exc}")
            if benchmark.status == BenchmarkStatus.DESIGNED:
                return
        benchmark.status = BenchmarkStatus.PARTIAL if benchmark.dimensions else BenchmarkStatus.FAILED
        self._event(benchmark, "design", "design_incomplete")

    def ground(self, benchmark: Benchmark, providers: list[SourceProvider]) -> None:
        actions = self.agents.ground(benchmark, providers)
        self._event(benchmark, "grounding", "agent_output", payload={"actions": [a.model_dump() for a in actions]})
        provider_by_id = {p.provider_id: p for p in providers}
        for action in actions:
            try:
                if action.grounding is not None and action.grounding.source_id in provider_by_id:
                    provider = provider_by_id[action.grounding.source_id]
                    provider_mode = getattr(provider, "source_mode", action.grounding.source_mode)
                    updates = {"source_mode": provider_mode}
                    if provider_mode.value == "generated_environment" and action.grounding.realization_capacity <= 0:
                        updates["realization_capacity"] = provider.capacity()
                    action = action.model_copy(update={"grounding": action.grounding.model_copy(update=updates)})
                apply_grounding(benchmark, action)
            except Exception as exc:
                self._event(benchmark, "grounding", "validation_error", str(exc), {"action": action.model_dump()})
                benchmark.warnings.append(f"grounding: {exc}")
        if not benchmark.groundings:
            benchmark.status = BenchmarkStatus.PARTIAL
        self._event(benchmark, "grounding", "grounding_complete", payload={
            "ready": sum(g.executable for g in benchmark.groundings),
            "total": len(benchmark.groundings),
        })

    def allocate(self, benchmark: Benchmark) -> None:
        decision = self.agents.allocate(benchmark)
        self._event(benchmark, "allocation", "agent_output", payload=decision.model_dump())
        try:
            apply_allocation(benchmark, decision)
        except Exception as exc:
            benchmark.status = BenchmarkStatus.PARTIAL
            benchmark.warnings.append(f"allocation: {exc}")
            self._event(benchmark, "allocation", "validation_error", str(exc))
            return
        allocated = sum(a.total_quota for a in benchmark.allocations)
        actual_shortfall = max(0, benchmark.user_goal.target_size - allocated)
        if actual_shortfall:
            benchmark.status = BenchmarkStatus.PARTIAL
            self._event(benchmark, "allocation", "shortfall", payload={"count": actual_shortfall})

    def execute(self, benchmark: Benchmark, providers: list[SourceProvider]) -> None:
        """Executor / Sample Realization only creates candidates.

        Verification & Control is deliberately a separate stage so the graph
        preserves the original role boundary.
        """
        by_id = {p.provider_id: p for p in providers}
        for allocation in benchmark.allocations:
            provider = by_id.get(allocation.source_id or "")
            if provider is None:
                self._event(benchmark, "executor", "missing_provider", payload={"source_id": allocation.source_id})
                benchmark.warnings.append(f"missing provider: {allocation.source_id}")
                continue
            offset = benchmark.provider_offsets.get(provider.provider_id, 0)
            realization_count = allocation.executable_quota + allocation.realization_quota
            samples = provider.sample(realization_count, offset=offset)
            benchmark.provider_offsets[provider.provider_id] = offset + len(samples)
            if len(samples) < realization_count:
                missing = realization_count - len(samples)
                benchmark.warnings.append(
                    f"provider {provider.provider_id} returned {len(samples)}/{realization_count} samples"
                )
                self._request_replenishment(
                    benchmark, allocation, missing, "provider returned fewer samples"
                )
            for sample in samples:
                try:
                    result = self.agents.execute(benchmark, allocation, provider, sample)
                    self._event(benchmark, "executor", "sample_result", payload=result.model_dump())
                    if result.item is not None:
                        if result.item.source_mode.value == "generated_environment":
                            result.item.covered_dimension_ids = [d.id for d in benchmark.dimensions if d.status == "active"]
                        benchmark.candidates.append(result.item)
                except Exception as exc:
                    self._event(benchmark, "executor", "sample_failed", str(exc), {"sample_id": sample.sample_id})
                    benchmark.warnings.append(f"sample {sample.sample_id}: {exc}")
                    self._request_replenishment(
                        benchmark, allocation, 1, f"executor sample failed: {exc}"
                    )

    def _request_replenishment(self, benchmark: Benchmark, allocation, count: int, reason: str) -> None:
        if not allocation.source_id or count <= 0:
            return
        for request in benchmark.replenishment_requests:
            if request.dimension_id == allocation.dimension_id and request.source_id == allocation.source_id:
                request.count += count
                request.reason = reason
                return
        benchmark.replenishment_requests.append(ReplenishmentRequest(
            dimension_id=allocation.dimension_id,
            source_id=allocation.source_id,
            count=count,
            reason=reason,
        ))
        self._event(benchmark, "verification", "replenishment_requested", payload={
            "dimension_id": allocation.dimension_id,
            "source_id": allocation.source_id,
            "count": count,
            "reason": reason,
        })

    def verify_pending(self, benchmark: Benchmark) -> None:
        """Verification & Control evaluates candidates and promotes or rejects them."""
        pending = list(benchmark.candidates)
        benchmark.candidates.clear()
        for item in pending:
            try:
                verification = self.agents.verify(benchmark, item)
                item.verification = verification.model_dump()
                item.warnings.extend(verification.warnings)
                item.status = ItemStatus(verification.status)
                self._event(benchmark, "verification", "verification_result", payload=verification.model_dump())
                if verification.valid or (self.config.accept_warnings and verification.status == "accepted_with_warnings"):
                    benchmark.items.append(item)
                else:
                    benchmark.rejected_items.append(item)
                    if verification.control_action == "replenish":
                        allocation = next((a for a in benchmark.allocations if a.dimension_id == item.dimension_id and a.source_id == item.source_id), None)
                        if allocation is not None:
                            self._request_replenishment(benchmark, allocation, 1, "verification requested replenish")
            except Exception as exc:
                item.status = ItemStatus.FAILED
                item.warnings.append(str(exc))
                benchmark.rejected_items.append(item)
                self._event(benchmark, "verification", "verification_failed", str(exc), {"item_id": item.item_id})
                benchmark.warnings.append(f"verification {item.item_id}: {exc}")

    def replenish(self, benchmark: Benchmark, providers: list[SourceProvider]) -> None:
        """Run bounded Executor → Verification rounds for control requests."""
        if not benchmark.replenishment_requests:
            return
        by_id = {p.provider_id: p for p in providers}
        allocations = {(a.dimension_id, a.source_id): a for a in benchmark.allocations}
        for round_no in range(self.config.max_item_attempts):
            requests = list(benchmark.replenishment_requests)
            benchmark.replenishment_requests.clear()
            if not requests:
                break
            self._event(benchmark, "verification", "replenishment_round", payload={
                "round": round_no, "requests": [r.model_dump() for r in requests]
            })
            for request in requests:
                provider = by_id.get(request.source_id)
                allocation = allocations.get((request.dimension_id, request.source_id))
                if provider is None or allocation is None:
                    benchmark.warnings.append(f"cannot replenish {request.dimension_id}: provider/allocation missing")
                    continue
                offset = benchmark.provider_offsets.get(provider.provider_id, 0)
                samples = provider.sample(request.count, offset=offset)
                benchmark.provider_offsets[provider.provider_id] = offset + len(samples)
                if len(samples) < request.count:
                    benchmark.warnings.append(
                        f"replenishment exhausted for {provider.provider_id}: {len(samples)}/{request.count}"
                    )
                for sample in samples:
                    try:
                        result = self.agents.execute(benchmark, allocation, provider, sample)
                        self._event(benchmark, "executor", "replenishment_sample", payload=result.model_dump())
                        if result.item is not None:
                            benchmark.candidates.append(result.item)
                    except Exception as exc:
                        benchmark.warnings.append(f"replenishment sample {sample.sample_id}: {exc}")
            self.verify_pending(benchmark)

    def finalize(self, benchmark: Benchmark) -> None:
        target = benchmark.user_goal.target_size
        item_warnings = any(item.warnings or item.status == ItemStatus.ACCEPTED_WITH_WARNINGS for item in benchmark.items)
        if len(benchmark.items) >= target and not benchmark.warnings and not item_warnings:
            benchmark.status = BenchmarkStatus.COMPLETED
        elif benchmark.items:
            benchmark.status = BenchmarkStatus.DEGRADED if benchmark.warnings or item_warnings else BenchmarkStatus.PARTIAL
        elif benchmark.status != BenchmarkStatus.FAILED:
            benchmark.status = BenchmarkStatus.PARTIAL
        self._event(benchmark, "orchestrator", "run_complete", payload={
            "status": benchmark.status.value,
            "items": len(benchmark.items),
            "target": target,
            "warnings": len(benchmark.warnings),
        })
