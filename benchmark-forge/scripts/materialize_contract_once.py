from __future__ import annotations

"""Run one fixed Contract through IR -> components -> linker.

This script intentionally performs no online retry and no Design/Grounding/
Allocation regeneration. After a bug fix, invoke it again with the same
benchmark.json Contract to compare the next attempt.
"""

import argparse
import json
import os
import time
from pathlib import Path

from benchmark_forge import EnvironmentIRCompilerAgent, PydanticAIRoleAgents, openai_compatible_model
from benchmark_forge.domain import BenchmarkItem, ExecutableTaskContract
from benchmark_forge.environment_ir import link_component_outputs
from benchmark_forge.octagon import OctagonKnowledgeBase
from benchmark_forge.component_agents import generate_component_outputs
from benchmark_forge.staging import normalize_octagon_scaffold, validate_scaffold


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="One fixed Contract materialization pass")
    parser.add_argument("benchmark_json", type=Path)
    parser.add_argument("--item-index", type=int, default=0)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--knowledge-db", type=Path, default=Path("run/octagon.sqlite3"))
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    load_env(Path(".env"))
    load_env(Path("../.env"))
    payload = json.loads(args.benchmark_json.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    item_payload = items[args.item_index]
    item = BenchmarkItem.model_validate(item_payload)
    contract = item.executable_task
    if contract is None:
        raise SystemExit("selected item has no executable_task")

    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "contract.json").write_text(contract.model_dump_json(indent=2), encoding="utf-8")
    started = time.monotonic()
    report: dict[str, object] = {
        "schema_version": "benchmark-forge.fixed-contract-materialization.v1",
        "status": "started",
        "contract_task_id": contract.task_id,
        "environment_id": contract.environment.environment_id,
        "steps": [],
    }

    model = openai_compatible_model(
        model_name=os.environ["LLM_MODEL"],
        base_url=os.environ["LLM_API_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
    )
    kb = OctagonKnowledgeBase(args.knowledge_db)

    try:
        report["steps"].append("contract_loaded")
        compiler = EnvironmentIRCompilerAgent(
            model=model, knowledge_base=kb, max_rewrites=0,
            timeout=args.timeout, retries=0,
        )
        ir = compiler.compile(contract)
        report["steps"].append("ir_compiled_and_frozen")
        (args.output_root / "environment-ir.json").write_text(ir.model_dump_json(indent=2), encoding="utf-8")

        # This is the only component generation path. No whole-bundle Agent.
        outputs = generate_component_outputs(
            model=model, item=item, ir=ir, scorer_design=None,
            timeout=args.timeout, retries=0,
        )
        report["steps"].append("components_generated")
        bundle = link_component_outputs(ir, outputs)
        report["steps"].append("linked")
        # Linker proves component ownership; deterministic Octagon normalization
        # then adapts the manifest to the loader contract before static checks.
        bundle = normalize_octagon_scaffold(bundle, item, ir)
        validation = validate_scaffold(bundle, item, ir)
        report["steps"].append("static_validated")
        report["static_validation"] = validation.model_dump(mode="json")
        bundle_root = args.output_root / "bundle"
        for file in bundle.files:
            destination = bundle_root / file.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(file.content, encoding="utf-8")
        report.update({
            "status": "success" if validation.valid else "failed",
            "ir_checksum": ir.ir_checksum,
            "files": [file.path for file in bundle.files],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        if not validation.valid:
            report.update({
                "failed_at": "static_validated",
                "error_type": "ScaffoldValidationError",
                "error": "; ".join(validation.errors)[:4_000],
            })
    except Exception as exc:
        report.update({
            "status": "failed",
            "failed_at": report["steps"][-1] if report["steps"] else "startup",
            "error_type": type(exc).__name__,
            "error": str(exc)[:4_000],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
    (args.output_root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
