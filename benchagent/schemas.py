"""Core data structures for the benchmark construction pipeline.

Mirrors the paper's object model:
  UserQuery -> SubtaskSet -> (Subtask, Dataset, TransformPlan, Quota) -> Samples
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# User input
# ---------------------------------------------------------------------------
class UserQuery(BaseModel):
    """A natural-language evaluation goal, e.g. the `user_queries/*.json` files."""

    id: str = Field(description="Unique identifier for this benchmark instance")
    description: str = Field(description="Evaluation intent in natural language")
    target_size: int = Field(ge=1, description="Total number of benchmark items to generate")


# ---------------------------------------------------------------------------
# Dataset pool
# ---------------------------------------------------------------------------
class DatasetCard(BaseModel):
    """Metadata for one candidate dataset in the pool (the `dataset card`)."""

    dataset_id: str
    name: str
    modalities: list[str] = Field(description='e.g. ["text"], ["image","text"], ["audio"]')
    io_schemas: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of {in_: [...], out: [...]} modality I/O schemas",
    )
    size_samples: int = 0
    description: str = ""
    card_text: str = ""
    tasks: list[str] = Field(default_factory=list)
    domain: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def modality_label(self) -> str:
        return "+".join(sorted(self.modalities))


class DatasetInstance(BaseModel):
    """A concrete sample drawn from a dataset (raw source data, un-transformed)."""

    dataset_id: str
    index: int
    fields: dict[str, Any] = Field(description="Raw fields of the sample, e.g. {context, image_path}")


# ---------------------------------------------------------------------------
# Planner outputs
# ---------------------------------------------------------------------------
class Subtask(BaseModel):
    """One atomic, testable evaluation dimension decomposed from the user query."""

    id: str = Field(description="Short unique id, e.g. 'multi_perspective_integration'")
    name: str
    description: str = Field(description="What capability this subtask evaluates")
    modalities: list[str] = Field(description="Input modalities required (text/image/audio/mixed)")
    answer_type: Literal["multiple_choice", "open_ended", "true_false"] = "multiple_choice"
    output_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="Expected output fields, e.g. {question, options, answer}",
    )
    status: Literal["proposed", "grounded", "rejected"] = "proposed"


class TransformStep(BaseModel):
    """One step in a transformation plan."""

    tool: str = Field(description="Tool name registered in the tool registry")
    params: dict[str, Any] = Field(default_factory=dict)


class TransformPlan(BaseModel):
    """A validated, executable transformation plan for a (subtask, dataset) pair."""

    steps: list[TransformStep]
    rationale: str = Field(default="", description="Why this plan realizes the subtask")


class Grounding(BaseModel):
    """A valid grounding: subtask realized on a dataset through a transformation plan.

    Paper notation: (s_i, d_i,j, t_i,j) with the guarantee that the plan passes
    the Score-and-Filter check.
    """

    subtask_id: str
    dataset_id: str
    plan: TransformPlan
    scores: dict[str, float] = Field(
        default_factory=dict,
        description="alignment / robustness / signal_preservation scores (1-5)",
    )


class AllocationItem(BaseModel):
    """(dataset, subtask, quota) triple decided by the Allocation Agent."""

    subtask_id: str
    dataset_id: str
    plan: TransformPlan
    quota: int = Field(ge=0)


class BenchmarkSpec(BaseModel):
    """B = {(s_i, G_i)}: the full, grounded, allocated benchmark specification."""

    user_query: UserQuery
    subtasks: list[Subtask]
    allocations: list[AllocationItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Executor outputs
# ---------------------------------------------------------------------------
class SampleState(BaseModel):
    """Mutable state of one sample as it flows through the executor."""

    index: int
    subtask_id: str
    dataset_id: str
    fields: dict[str, Any] = Field(default_factory=dict, description="Current fields")
    status: Literal["pending", "done", "failed", "verified"] = "pending"
    log: list[str] = Field(default_factory=list)


class BenchmarkSample(BaseModel):
    """One final, verified benchmark item."""

    subtask_id: str
    dataset_id: str
    sample_index: int
    question: str
    context: Optional[str] = None
    media: list[dict[str, Any]] = Field(
        default_factory=list,
        description='e.g. [{"type": "image", "path": "..."}, {"type": "audio", "path": "..."}]',
    )
    options: Optional[list[str]] = None
    answer: str
    answer_type: str = "multiple_choice"
    meta: dict[str, Any] = Field(default_factory=dict)
