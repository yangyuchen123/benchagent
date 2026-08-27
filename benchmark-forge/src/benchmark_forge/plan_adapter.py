from __future__ import annotations

"""Adapters from the legacy Benchmark state to comparison-safe plan objects."""

from .domain import Benchmark
from .preference_alignment import BenchmarkPlanCandidate


def benchmark_to_plan(benchmark: Benchmark, *, plan_id: str) -> BenchmarkPlanCandidate:
    """Project a generated branch into a public, comparable planning blueprint.

    The projection intentionally omits provider internals, hidden answers,
    private paths and scorer implementation details. It is a planning summary,
    not a replacement for the executable Benchmark object.
    """
    capabilities = sorted({dimension.capability for dimension in benchmark.dimensions})
    task_forms = {dimension.task_form for dimension in benchmark.dimensions}
    task_form = "executable_task" if "executable_task" in task_forms else (
        "hybrid" if "hybrid" in task_forms else "static_question"
    )
    behavior_requirements = [
        f"{dimension.id}: {dimension.name} — {dimension.description}"
        for dimension in benchmark.dimensions
        if dimension.status != "discarded"
    ]
    environment_requirements = sorted({
        f"{grounding.source_mode.value}:{grounding.source_id or 'unresolved'}"
        for grounding in benchmark.groundings
    })
    artifact_requirements: set[str] = set()
    scoring_intent: set[str] = set()
    for item in benchmark.candidates + benchmark.items:
        if item.executable_task is None:
            continue
        artifact_requirements.update(artifact.path for artifact in item.executable_task.artifacts)
        scoring_intent.update(dimension.name for dimension in item.executable_task.scoring.dimensions)
    return BenchmarkPlanCandidate(
        plan_id=plan_id,
        title=f"Plan for {benchmark.user_goal.goal_id}",
        capability=", ".join(capabilities) or "general_reasoning",
        task_form=task_form,
        task_description=benchmark.user_goal.description,
        environment_description="; ".join(environment_requirements),
        behavior_requirements=behavior_requirements,
        artifact_requirements=sorted(artifact_requirements),
        scoring_intent=sorted(scoring_intent),
        difficulty_intent=(
            "generated from multi-stage design/grounding/allocation/execution and verification"
        ),
        cost_intent=f"target_size={benchmark.user_goal.target_size}",
        provenance={
            "source": "legacy-benchmark-adapter",
            "benchmark_branch_id": benchmark.benchmark_id,
            "dimension_count": len(benchmark.dimensions),
            "item_count": len(benchmark.items),
        },
    )
