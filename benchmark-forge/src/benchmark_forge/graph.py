"""Typed Pydantic Graph wrapper for the MVP lifecycle.

The graph deliberately keeps the original five role stages. It is a thin
orchestration layer around the already-tested reducers and orchestrator stage
methods; it does not introduce a second domain state model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_graph import Graph, GraphBuilder
from pydantic_graph.step import StepContext

from .domain import Benchmark
from .orchestrator import BenchmarkOrchestrator
from .providers import SourceProvider


@dataclass
class GraphDeps:
    orchestrator: BenchmarkOrchestrator
    providers: list[SourceProvider]


async def design_step(ctx: StepContext[Benchmark, GraphDeps, None]) -> Benchmark:
    ctx.deps.orchestrator.design(ctx.state)
    return ctx.state


async def grounding_step(ctx: StepContext[Benchmark, GraphDeps, Benchmark]) -> Benchmark:
    ctx.deps.orchestrator.ground(ctx.state, ctx.deps.providers)
    return ctx.state


async def allocation_step(ctx: StepContext[Benchmark, GraphDeps, Benchmark]) -> Benchmark:
    ctx.deps.orchestrator.allocate(ctx.state)
    return ctx.state


async def executor_step(ctx: StepContext[Benchmark, GraphDeps, Benchmark]) -> Benchmark:
    ctx.deps.orchestrator.execute(ctx.state, ctx.deps.providers)
    return ctx.state


async def verification_control_step(ctx: StepContext[Benchmark, GraphDeps, Benchmark]) -> Benchmark:
    ctx.deps.orchestrator.verify_pending(ctx.state)
    ctx.deps.orchestrator.replenish(ctx.state, ctx.deps.providers)
    ctx.deps.orchestrator.finalize(ctx.state)
    return ctx.state


def build_graph() -> Graph[Any, Any, Any, Any]:
    builder = GraphBuilder(
        name="benchmark_forge_mvp",
        state_type=Benchmark,
        deps_type=GraphDeps,
        input_type=None,
        output_type=Benchmark,
        auto_instrument=False,
    )
    design = builder.step(design_step, node_id="design")
    grounding = builder.step(grounding_step, node_id="grounding")
    allocation = builder.step(allocation_step, node_id="allocation")
    executor = builder.step(executor_step, node_id="executor")
    verification = builder.step(verification_control_step, node_id="verification_control")

    builder.add_edge(builder.start_node, design)
    builder.add_edge(design, grounding)
    builder.add_edge(grounding, allocation)
    builder.add_edge(allocation, executor)
    builder.add_edge(executor, verification)
    builder.add_edge(verification, builder.end_node)
    return builder.build()


def run_graph_sync(benchmark: Benchmark, deps: GraphDeps) -> Benchmark:
    return build_graph().run_sync(state=benchmark, deps=deps, inputs=None)
