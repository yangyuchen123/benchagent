"""Small file-based checkpoint contract for MVP resume behavior."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .domain import Benchmark

CheckpointStage = Literal[
    "design",
    "grounding",
    "allocation",
    "executor",
    "verification",
    "replenish",
    "finalize",
]


class BenchmarkCheckpoint(BaseModel):
    schema_version: str = "benchmark.forge.checkpoint.v1"
    benchmark_id: str
    next_stage: CheckpointStage
    benchmark: Benchmark
    saved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def save_checkpoint(path: str | Path, benchmark: Benchmark, next_stage: CheckpointStage) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = BenchmarkCheckpoint(
        benchmark_id=benchmark.benchmark_id,
        next_stage=next_stage,
        benchmark=benchmark,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_checkpoint(path: str | Path) -> BenchmarkCheckpoint:
    return BenchmarkCheckpoint.model_validate_json(Path(path).read_text(encoding="utf-8"))
