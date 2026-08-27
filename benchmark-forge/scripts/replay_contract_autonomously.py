from __future__ import annotations

"""Autonomously replay one accepted Contract below the semantic boundary."""

import argparse
import json
import os
from pathlib import Path

from benchmark_forge import (
    BenchmarkItem, EnvironmentIRCompilerAgent, FixedContractReplayWorkflow,
    LocalPytestBackend, MaterializationPolicy, MaterializationWorkflow,
    PydanticAIRoleAgents, openai_compatible_model,
)
from benchmark_forge.domain import ExecutableTaskContract
from benchmark_forge.octagon import OctagonKnowledgeBase


def load_env() -> None:
    for path in (Path(".env"), Path("../.env")):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
                key, value = raw.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def load_item(path: Path, item_index: int) -> BenchmarkItem:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "items" in payload:
        return BenchmarkItem.model_validate(payload["items"][item_index])
    if "executable_task" in payload:
        return BenchmarkItem.model_validate(payload)
    contract = ExecutableTaskContract.model_validate(payload)
    return BenchmarkItem(
        item_id=contract.task_id, dimension_id=contract.scoring.dimensions[0].name,
        covered_dimension_ids=[dimension.name for dimension in contract.scoring.dimensions],
        source_mode="generated_environment", source_id="fixed-contract-replay",
        item_kind="executable_task", answer_type="artifact", executable_task=contract,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay an existing Contract through IR/components/link/validation without regenerating its design",
    )
    parser.add_argument("input_json", type=Path, help="benchmark.json, BenchmarkItem JSON, or ExecutableTaskContract JSON")
    parser.add_argument("--item-index", type=int, default=0)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--knowledge-db", type=Path, default=Path("run/octagon.sqlite3"))
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--generation-attempts", type=int, default=2)
    parser.add_argument("--component-repairs", type=int, default=2)
    parser.add_argument("--link-cycles", type=int, default=5)
    parser.add_argument(
        "--run-local-tests", action="store_true",
        help="Development only: execute generated pytest code locally; production uses an isolated peer eval-system",
    )
    args = parser.parse_args()
    load_env()
    item = load_item(args.input_json, args.item_index)
    model = openai_compatible_model(
        model_name=os.environ["LLM_MODEL"], base_url=os.environ["LLM_API_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
    )
    kb = OctagonKnowledgeBase(args.knowledge_db)
    agents = PydanticAIRoleAgents(model=model, knowledge_base=kb, llm_timeout=args.timeout)
    compiler = EnvironmentIRCompilerAgent(
        model=model, knowledge_base=kb, max_rewrites=1, timeout=args.timeout, retries=0,
    )
    materializer = MaterializationWorkflow(
        component_generator=lambda *, component_id, item, ir, dependency_outputs: agents.generate_environment_component(
            item, ir, component_id, dependency_outputs,
        ),
        component_repairer=lambda *, component_id, item, ir, current, review, dependency_outputs: agents.repair_environment_component(
            item, ir, component_id, current, review, None, dependency_outputs,
        ),
        diagnoser=lambda *, item, ir, outputs, observation: agents.diagnose_materialization_failure(
            item, ir, outputs, observation,
        ),
        policy=MaterializationPolicy(
            max_generation_attempts_per_component=args.generation_attempts,
            max_repairs_per_component=args.component_repairs,
            max_link_cycles=args.link_cycles,
            run_bundle_tests=args.run_local_tests,
        ),
        test_backend=LocalPytestBackend(timeout_seconds=args.timeout) if args.run_local_tests else None,
    )
    report = FixedContractReplayWorkflow(compiler=compiler, materializer=materializer).run(
        item=item, output_root=args.output_root,
    )
    print(report.model_dump_json(indent=2))
    return 0 if report.status == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
