from __future__ import annotations

"""Double-planning and pre-materialization selection gate.

This module deliberately stays independent from the legacy Benchmark state
machine. It provides a reusable contract that can later be inserted between
Design and Executor without changing the original five role reducers.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .preference_alignment import (
    BenchmarkPlanCandidate,
    PreferenceAlignmentDecision,
)


class PlanningPairModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanProvenance(PlanningPairModel):
    prompt_checksum: str
    model_id: str
    knowledge_snapshot: str
    branch: Literal["a", "b"]
    generation_index: int = Field(ge=1)


class BenchmarkPlanPair(PlanningPairModel):
    pair_id: str
    prompt_checksum: str
    model_id: str
    knowledge_snapshot: str
    plan_a: BenchmarkPlanCandidate
    plan_b: BenchmarkPlanCandidate
    provenance_a: PlanProvenance
    provenance_b: PlanProvenance
    similarity_score: float = Field(ge=0, le=1)
    resample_count: int = Field(default=0, ge=0)
    status: Literal["ready", "insufficient_diversity"] = "ready"

    @model_validator(mode="after")
    def validate_pair(self) -> "BenchmarkPlanPair":
        if self.plan_a.plan_id == self.plan_b.plan_id:
            raise ValueError("planning pair requires distinct plan ids")
        if self.provenance_a.prompt_checksum != self.prompt_checksum or self.provenance_b.prompt_checksum != self.prompt_checksum:
            raise ValueError("both plans must use the same prompt checksum")
        if self.provenance_a.model_id != self.model_id or self.provenance_b.model_id != self.model_id:
            raise ValueError("both plans must use the same model")
        if self.provenance_a.knowledge_snapshot != self.knowledge_snapshot or self.provenance_b.knowledge_snapshot != self.knowledge_snapshot:
            raise ValueError("both plans must use the same knowledge snapshot")
        return self


class PlanGenerator(Protocol):
    def __call__(self, prompt: str) -> BenchmarkPlanCandidate: ...


class PlanningPairError(RuntimeError):
    pass


class InsufficientPlanDiversity(PlanningPairError):
    pass


def prompt_checksum(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _tokens(plan: BenchmarkPlanCandidate) -> set[str]:
    payload = plan.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    return set(re.findall(r"[a-z0-9_]+", text))


def plan_similarity(plan_a: BenchmarkPlanCandidate, plan_b: BenchmarkPlanCandidate) -> float:
    """Return a transparent token Jaccard similarity in [0, 1]."""
    left, right = _tokens(plan_a), _tokens(plan_b)
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


@dataclass
class DoublePlanningService:
    """Generate two independent plans from the exact same prompt.

    ``generator`` is called independently for every branch. The service does
    not alter the prompt to force differences; if plans are too similar it
    repeats the same prompt up to ``max_resamples`` times.
    """

    generator: PlanGenerator
    model_id: str
    knowledge_snapshot: str
    similarity_threshold: float = 0.92
    max_resamples: int = 2

    def generate_pair(self, prompt: str, *, pair_id: str = "planning-pair") -> BenchmarkPlanPair:
        checksum = prompt_checksum(prompt)
        generation_index = 1
        plan_a = self.generator(prompt)
        plan_b = self.generator(prompt)
        score = plan_similarity(plan_a, plan_b)
        resamples = 0
        while score >= self.similarity_threshold and resamples < self.max_resamples:
            resamples += 1
            generation_index += 1
            # The prompt is intentionally byte-for-byte unchanged.
            plan_b = self.generator(prompt)
            score = plan_similarity(plan_a, plan_b)
        status = "ready" if score < self.similarity_threshold else "insufficient_diversity"
        return BenchmarkPlanPair(
            pair_id=pair_id,
            prompt_checksum=checksum,
            model_id=self.model_id,
            knowledge_snapshot=self.knowledge_snapshot,
            plan_a=plan_a,
            plan_b=plan_b,
            provenance_a=PlanProvenance(
                prompt_checksum=checksum, model_id=self.model_id,
                knowledge_snapshot=self.knowledge_snapshot, branch="a", generation_index=1,
            ),
            provenance_b=PlanProvenance(
                prompt_checksum=checksum, model_id=self.model_id,
                knowledge_snapshot=self.knowledge_snapshot, branch="b", generation_index=generation_index,
            ),
            similarity_score=score,
            resample_count=resamples,
            status=status,
        )


class MaterializationGate:
    """Only a selected plan may cross into Executor/materialization."""

    @staticmethod
    def authorize(
        pair: BenchmarkPlanPair,
        decision: PreferenceAlignmentDecision,
    ) -> BenchmarkPlanCandidate:
        if pair.status != "ready":
            raise PlanningPairError("cannot materialize an insufficient-diversity planning pair")
        if decision.control_action not in {"select", "select_with_warnings"}:
            raise PlanningPairError(
                f"materialization blocked by alignment action: {decision.control_action}"
            )
        if decision.selected_plan_id not in {pair.plan_a.plan_id, pair.plan_b.plan_id}:
            raise PlanningPairError("alignment selected a plan outside the planning pair")
        return pair.plan_a if decision.selected_plan_id == pair.plan_a.plan_id else pair.plan_b
