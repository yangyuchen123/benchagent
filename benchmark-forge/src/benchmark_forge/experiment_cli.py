from __future__ import annotations

import argparse
import os
from pathlib import Path

from .experiments import ABExperimentRunner, DEFAULT_CASES
from .octagon import EnvironmentCatalog, OctagonKnowledgeBase
from .pydantic_ai_adapter import openai_compatible_model


def _load_dotenv(path: str | Path) -> None:
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run blinded with-KB vs without-KB benchmark generation A/B")
    parser.add_argument("--octagon-root", required=True)
    parser.add_argument("--knowledge-base", required=True)
    parser.add_argument("--output-root", default="run/ab-kb")
    parser.add_argument("--dotenv", default="../.env")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--rebuild-index", action="store_true")
    args = parser.parse_args()

    _load_dotenv(args.dotenv)
    model_id = args.model or os.environ.get("LLM_MODEL", "gpt-5.6-luna")
    base_url = args.base_url or os.environ.get("LLM_API_BASE_URL") or os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not base_url or not api_key or api_key == "replace-with-your-key":
        parser.error("A real LLM base URL and API key are required via environment/.env")

    catalog = EnvironmentCatalog(args.octagon_root)
    kb = OctagonKnowledgeBase(args.knowledge_base)
    if args.rebuild_index or kb.count() == 0:
        print(f"indexed {kb.index_catalog(catalog, replace=True)} chunks")
    model = openai_compatible_model(model_name=model_id, base_url=base_url, api_key=api_key)
    report = ABExperimentRunner(
        model=model,
        catalog=catalog,
        knowledge_base=kb,
        output_root=Path(args.output_root),
        model_id=model_id,
        use_judge=not args.no_judge,
    ).run(DEFAULT_CASES, repeats=args.repeats)
    print(Path(args.output_root) / "REPORT.md")
    print(report.summaries)


if __name__ == "__main__":
    main()
