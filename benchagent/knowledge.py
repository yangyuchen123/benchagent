"""Knowledge base: few-shot examples that guide the LLM tools.

Motivation: letting the LLM generate cases from scratch every time both costs
tokens and yields unstable quality. Instead, high-quality examples — either
hand-curated seeds or samples that already passed the verification layer — are
stored here and injected into the LLM-tool prompts as few-shot demonstrations.

The auto-ingestion loop:
    generate → verify → (pass) → ingest into KB → better few-shot → better generate

Examples are keyed by a category string so injection stays diverse:
    "<tool_name>/<answer_type>"   e.g. "question_generation/multiple_choice"
"""
from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .schemas import BenchmarkSample

log = logging.getLogger(__name__)


@dataclass
class Example:
    category: str                  # e.g. "question_generation/multiple_choice"
    input_fields: dict[str, Any]   # what the tool receives (trimmed)
    output: dict[str, Any]         # the tool's expected output
    source: str = "seed"           # "seed" | "auto" | subtask id
    quality: float = 1.0


class KnowledgeBase:
    def __init__(self, path: Optional[str] = None,
                 max_per_category: int = 20, seed: int = 0):
        self.path = path
        self.max_per_category = max_per_category
        self._rng = random.Random(seed)
        self._examples: dict[str, list[Example]] = {}
        if path and os.path.exists(path):
            self.load()

    # ------------------------------------------------------------------ store
    def add(self, category: str, input_fields: dict, output: dict,
            source: str = "seed", quality: float = 1.0) -> None:
        ex = Example(category, dict(input_fields), dict(output), source, quality)
        bucket = self._examples.setdefault(category, [])
        bucket.append(ex)
        if len(bucket) > self.max_per_category:
            # keep the highest-quality tail
            bucket.sort(key=lambda e: e.quality, reverse=True)
            del bucket[self.max_per_category:]

    def query(self, category: str, k: int = 2) -> list[Example]:
        """Sample up to k examples from a category (diversity via rotation)."""
        bucket = self._examples.get(category, [])
        if not bucket:
            return []
        k = min(k, len(bucket))
        self._rng.shuffle(bucket)
        return bucket[:k]

    def categories(self) -> list[str]:
        return sorted(self._examples)

    # ------------------------------------------------------------ ingestion
    def ingest_sample(self, sample: BenchmarkSample, source: str = "auto",
                      quality: float = 1.0) -> None:
        """Sink a verified sample into the KB as few-shot demonstrations.

        A verified MC sample yields two entries:
          - question_generation:  context -> {question, answer}
          - distractor_generation: context+question+answer -> {distractors}
        """
        if not sample.question or not sample.answer:
            return
        at = sample.answer_type
        ctx = sample.context or ""
        if at == "multiple_choice" and sample.options:
            distractors = [o for o in sample.options if o != sample.answer]
            if distractors:
                self.add(
                    f"distractor_generation/{at}",
                    {"context": ctx, "question": sample.question, "answer": sample.answer},
                    {"distractors": distractors},
                    source=source, quality=quality,
                )
        self.add(
            f"question_generation/{at}",
            {"context": ctx},
            {"question": sample.question, "answer": sample.answer},
            source=source, quality=quality,
        )

    # ------------------------------------------------------------ persistence
    def persist(self) -> None:
        if not self.path:
            return
        os.makedirs(self.path, exist_ok=True)
        out = os.path.join(self.path, "examples.jsonl")
        with open(out, "w", encoding="utf-8") as f:
            for bucket in self._examples.values():
                for ex in bucket:
                    f.write(json.dumps({
                        "category": ex.category,
                        "input_fields": ex.input_fields,
                        "output": ex.output,
                        "source": ex.source,
                        "quality": ex.quality,
                    }, ensure_ascii=False) + "\n")
        log.info("KnowledgeBase: persisted %d examples to %s",
                 sum(len(b) for b in self._examples.values()), out)

    def load(self) -> None:
        p = os.path.join(self.path, "examples.jsonl")
        if not os.path.exists(p):
            return
        n = 0
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                self._examples.setdefault(d["category"], []).append(Example(
                    category=d["category"],
                    input_fields=d.get("input_fields", {}),
                    output=d.get("output", {}),
                    source=d.get("source", "seed"),
                    quality=d.get("quality", 1.0),
                ))
                n += 1
        log.info("KnowledgeBase: loaded %d examples from %s", n, p)

    def stats(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._examples.items()}
