from __future__ import annotations

import json
from pathlib import Path
from .domain import Benchmark


def save_benchmark(root: str | Path, benchmark: Benchmark) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "benchmark.json"
    path.write_text(json.dumps(benchmark.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "events.jsonl").write_text(
        "".join(json.dumps(e.model_dump(mode="json"), ensure_ascii=False) + "\n" for e in benchmark.events),
        encoding="utf-8",
    )
    return path


def load_benchmark(path: str | Path) -> Benchmark:
    return Benchmark.model_validate_json(Path(path).read_text(encoding="utf-8"))
