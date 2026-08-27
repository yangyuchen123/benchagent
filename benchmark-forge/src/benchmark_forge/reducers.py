from __future__ import annotations

from .actions import AllocationDecision, DesignAction, GroundingAction
from .domain import Benchmark, BenchmarkDimension, BenchmarkGrounding, BenchmarkStatus


class StateError(ValueError):
    pass


def apply_design(benchmark: Benchmark, action: DesignAction) -> Benchmark:
    dims = list(benchmark.dimensions)
    if action.action == "add_dimension":
        if action.dimension is None:
            raise StateError("add_dimension requires dimension")
        if any(d.id == action.dimension.id for d in dims):
            raise StateError(f"duplicate dimension: {action.dimension.id}")
        dims.append(action.dimension)
    elif action.action == "revise_dimension":
        if not action.dimension_id:
            raise StateError("revise_dimension requires dimension_id")
        for i, dim in enumerate(dims):
            if dim.id == action.dimension_id:
                dims[i] = dim.model_copy(update=action.changes)
                break
        else:
            raise StateError(f"unknown dimension: {action.dimension_id}")
    elif action.action == "discard_dimension":
        if not action.dimension_id:
            raise StateError("discard_dimension requires dimension_id")
        dims = [d for d in dims if d.id != action.dimension_id]
    elif action.action == "finish_design":
        if not dims:
            raise StateError("cannot finish design without dimensions")
        dims = [d.model_copy(update={"status": "active"}) for d in dims]
        benchmark.status = BenchmarkStatus.DESIGNED
    benchmark.dimensions = dims
    return benchmark


def apply_grounding(benchmark: Benchmark, action: GroundingAction) -> Benchmark:
    if action.action == "finish_grounding":
        return benchmark
    if action.grounding is None:
        raise StateError(f"{action.action} requires grounding")
    if action.grounding.dimension_id not in {d.id for d in benchmark.dimensions}:
        raise StateError("grounding references unknown dimension")
    benchmark.groundings = [g for g in benchmark.groundings
                            if g.dimension_id != action.grounding.dimension_id
                            or g.source_id != action.grounding.source_id]
    benchmark.groundings.append(action.grounding)
    return benchmark


def apply_allocation(benchmark: Benchmark, decision: AllocationDecision) -> Benchmark:
    ids = {d.id for d in benchmark.dimensions}
    for alloc in decision.allocations:
        if alloc.dimension_id not in ids:
            raise StateError(f"allocation references unknown dimension: {alloc.dimension_id}")
        if alloc.executable_quota < 0 or alloc.realization_quota < 0 or alloc.deferred_quota < 0:
            raise StateError("quota must be non-negative")
    keys = [(a.dimension_id, a.source_id) for a in decision.allocations]
    if len(keys) != len(set(keys)):
        raise StateError("duplicate allocation key")
    # Deterministic global quota guard: an LLM may accidentally allocate the
    # full target independently for multiple dimensions. Keep proposal order,
    # trim overflow, and preserve the valid prefix rather than corrupting the
    # Benchmark or silently exceeding the requested target size.
    remaining = benchmark.user_goal.target_size
    normalized: list[Allocation] = []
    overflow = 0
    # One generated environment task may evaluate several dimensions at once.
    # Collapse repeated allocations to the same blueprint source rather than
    # treating dimension coverage as additional benchmark item quota.
    proposals = []
    generated_sources: set[str] = set()
    for alloc in decision.allocations:
        if alloc.source_mode.value == "generated_environment" and alloc.source_id:
            if alloc.source_id in generated_sources:
                continue
            generated_sources.add(alloc.source_id)
        proposals.append(alloc)
    for alloc in proposals:
        allowed = min(alloc.total_quota, remaining)
        executable = min(alloc.executable_quota, allowed)
        realization = min(alloc.realization_quota, allowed - executable)
        deferred = min(alloc.deferred_quota, allowed - executable - realization)
        # A generated-environment blueprint is actionable by Benchmark Forge
        # even when it is not yet runtime-executable. Convert deferred proposal
        # into contract realization rather than stopping before Executor.
        if alloc.source_mode.value == "generated_environment" and not executable and not realization and deferred:
            realization, deferred = deferred, 0
        if allowed < alloc.total_quota:
            overflow += alloc.total_quota - allowed
        if executable or realization or deferred:
            normalized.append(alloc.model_copy(update={
                "executable_quota": executable,
                "realization_quota": realization,
                "deferred_quota": deferred,
            }))
            remaining -= executable + realization + deferred
    benchmark.allocations = normalized
    if remaining:
        benchmark.warnings.append(f"allocation shortfall={remaining}")
    if overflow:
        benchmark.warnings.append(f"allocation overflow trimmed={overflow}")
    return benchmark
