from __future__ import annotations

import argparse
import json
from pathlib import Path

from .orchestrator import BenchmarkOrchestrator
from .providers import DatasetProvider, ProceduralProvider
from .domain import UserGoal
from .pydantic_ai_adapter import openai_compatible_model
from .pydantic_agents import PydanticAIRoleAgents
from .octagon import EnvironmentCatalog, OctagonKnowledgeBase, RAGEnvironmentBlueprintProvider
from .staging import EnvironmentCandidateRegistry, stage_generated_candidates
from .service import materialize_candidates_with_scorer_control
from .persistence import save_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Benchmark Forge deterministic MVP")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--target-size", type=int, default=10)
    parser.add_argument("--artifact-root", default="run/demo")
    parser.add_argument("--source", choices=["auto", "rag-environment", "procedural", "empty", "json"], default="auto")
    parser.add_argument("--data", help="JSON list of source rows when --source=json")
    parser.add_argument("--llm-model", help="Use PydanticAI with this model name")
    parser.add_argument("--llm-base-url", help="OpenAI-compatible base URL")
    parser.add_argument("--llm-api-key", help="API key; prefer the LLM_API_KEY environment variable")
    parser.add_argument("--octagon-root", help="Read-only agent-octagon-envs catalog root")
    parser.add_argument("--knowledge-base", help="SQLite RAG index path")
    parser.add_argument("--rebuild-knowledge-base", action="store_true", help="Reindex safe Octagon files before running")
    args = parser.parse_args()

    orchestrator = BenchmarkOrchestrator()
    environment_catalog = EnvironmentCatalog(args.octagon_root) if args.octagon_root else None
    knowledge_base = OctagonKnowledgeBase(args.knowledge_base) if args.knowledge_base else None
    if knowledge_base and environment_catalog and (args.rebuild_knowledge_base or knowledge_base.count() == 0):
        knowledge_base.index_catalog(environment_catalog, replace=True)
    providers = []
    source = args.source
    if source == "auto":
        source = "rag-environment" if knowledge_base and args.llm_model else "procedural"
    if source == "rag-environment":
        if knowledge_base is None:
            parser.error("--source=rag-environment requires --knowledge-base")
        providers = [RAGEnvironmentBlueprintProvider(args.goal, knowledge_base, capacity_hint=args.target_size)]
    elif source == "procedural":
        providers = [ProceduralProvider(capacity_hint=args.target_size)]
    elif source == "json":
        if not args.data:
            parser.error("--source=json requires --data")
        rows = json.loads(Path(args.data).read_text(encoding="utf-8"))
        providers = [DatasetProvider("json_source", rows)]
    if args.llm_model:
        import os
        base_url = args.llm_base_url or os.environ.get("LLM_API_BASE_URL") or os.environ.get("LLM_BASE_URL")
        api_key = args.llm_api_key or os.environ.get("LLM_API_KEY")
        if not base_url or not api_key:
            parser.error("--llm-model requires --llm-base-url and --llm-api-key or LLM_* environment variables")
        orchestrator.agents = PydanticAIRoleAgents(
            model=openai_compatible_model(model_name=args.llm_model, base_url=base_url, api_key=api_key),
            environment_catalog=environment_catalog,
            knowledge_base=knowledge_base,
        )
        orchestrator.config.model_id = args.llm_model

    benchmark = orchestrator.run(
        UserGoal(goal_id="cli", description=args.goal, target_size=args.target_size),
        providers,
        artifact_root=args.artifact_root,
    )
    registry = EnvironmentCandidateRegistry(Path(args.artifact_root) / "environment-candidates")
    candidates = stage_generated_candidates(benchmark, registry)
    materialize_candidates_with_scorer_control(
        agents=orchestrator.agents, benchmark=benchmark, registry=registry, candidates=candidates,
    )
    if candidates:
        benchmark.manifest["environment_candidates"] = [
            {"candidate_id": c.candidate_id, "status": registry.load(c.candidate_id).status.value, "registry_root": str(registry.root)}
            for c in candidates
        ]
        save_benchmark(args.artifact_root, benchmark)
    print(json.dumps({
        "benchmark_id": benchmark.benchmark_id,
        "status": benchmark.status.value,
        "items": len(benchmark.items),
        "target_size": benchmark.user_goal.target_size,
        "warnings": benchmark.warnings,
        "artifact_root": args.artifact_root,
        "environment_candidates": benchmark.manifest.get("environment_candidates", []),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
