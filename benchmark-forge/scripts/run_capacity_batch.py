from __future__ import annotations

"""Run a small real capacity batch and collect per-stage outcome statistics."""

import argparse
import json
import os
import time
from pathlib import Path

from benchmark_forge import (
    BenchmarkGenerationService,
    CapabilityId,
    DEFAULT_CAPACITY_LIBRARY,
    EnvironmentIRCompilerAgent,
    PydanticAIRoleAgents,
    RunConfig,
    openai_compatible_model,
)
from benchmark_forge.octagon import OctagonKnowledgeBase


def load_env() -> None:
    for path in (Path(".env"), Path("../.env")):
        if path.exists():
            for raw in path.read_text(encoding="utf-8").splitlines():
                if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
                    key, value = raw.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


def goal_for(capability_id: CapabilityId) -> str:
    definition = DEFAULT_CAPACITY_LIBRARY.get(capability_id)
    return (
        f"生成一个评测 Agent {definition.name_zh}（{definition.name_en}）的开放、可执行 benchmark。"
        f"任务不能是选择题，必须包含真实的公开工具调用、可观察状态或轨迹、最终 artifact 和可执行评分。"
        f"核心构造：{definition.construct_definition}。"
        f"必须观察：{'；'.join(definition.observable_behaviors)}。"
        f"避免反模式：{'；'.join(definition.anti_patterns)}。"
        f"被测 Agent 必须使用自己的原生能力：{'；'.join(definition.required_agent_capabilities) or '无额外原生能力要求'}；"
        f"Benchmark 不得手写或模拟这些原生能力。"
        f"环境只应提供：{'；'.join(definition.required_environment_features)}。"
        f"评分维度：{'；'.join(definition.scoring_dimensions)}。"
        f"可加入扰动：{'；'.join(definition.perturbations)}。"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capabilities", nargs="+", default=[
        "instruction_following", "robustness_fault_tolerance",
        "delegation_quality", "context_compression_fidelity",
    ])
    parser.add_argument("--output-root", type=Path, default=Path("run/capacity-batch-20260826"))
    args = parser.parse_args()
    load_env()
    root = args.output_root
    root.mkdir(parents=True, exist_ok=True)
    capabilities = [CapabilityId(value) for value in args.capabilities]
    model = openai_compatible_model(
        model_name=os.environ["LLM_MODEL"],
        base_url=os.environ["LLM_API_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
    )
    kb = OctagonKnowledgeBase("run/octagon.sqlite3")
    agents = PydanticAIRoleAgents(model=model, knowledge_base=kb, llm_timeout=90)
    compiler = EnvironmentIRCompilerAgent(
        model=model, knowledge_base=kb, max_rewrites=0, timeout=90, retries=0,
    )
    service = BenchmarkGenerationService(
        agents=agents, knowledge_base=kb,
        config=RunConfig(model_id=os.environ["LLM_MODEL"]),
        ir_compiler=compiler, max_scorer_repair_rounds=0,
    )
    results: list[dict[str, object]] = []
    for capability_id in capabilities:
        slug = capability_id.value
        out = root / slug
        started = time.monotonic()
        record: dict[str, object] = {
            "capability_id": slug,
            "goal": goal_for(capability_id),
            "output_root": str(out),
        }
        try:
            benchmark = service.generate(
                record["goal"], target_size=1,
                benchmark_id=f"capacity-{slug}-20260826",
                artifact_root=out,
            )
            record.update({
                "status": benchmark.status.value,
                "items": len(benchmark.items),
                "rejected_items": len(benchmark.rejected_items),
                "warnings": benchmark.warnings,
            })
            candidates = []
            candidate_root = out / "environment-candidates"
            for candidate_file in candidate_root.glob("*/candidate.json"):
                candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
                candidates.append({
                    "candidate_id": candidate["candidate_id"],
                    "status": candidate["status"],
                    "has_ir": candidate.get("environment_ir") is not None,
                    "checks": [(check["check_id"], check["status"]) for check in candidate.get("checks", [])],
                })
            record["candidates"] = candidates
        except Exception as exc:
            record.update({"status": "process_failed", "error_type": type(exc).__name__, "error": str(exc)[:4000]})
        record["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (out / "batch-record.json").parent.mkdir(parents=True, exist_ok=True)
        (out / "batch-record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(record)
        print(json.dumps(record, ensure_ascii=False, indent=2), flush=True)
    summary = {
        "schema_version": "benchmark-forge.capacity-batch-report.v1",
        "requested_capabilities": [item.value for item in capabilities],
        "completed_runs": len(results),
        "successful_processes": sum(result.get("status") not in {"process_failed"} for result in results),
        "results": results,
    }
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
