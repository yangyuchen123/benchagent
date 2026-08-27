from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field
from .domain import BenchmarkDimension, BenchmarkGrounding, BenchmarkItem, Allocation


class DesignAction(BaseModel):
    action: Literal["add_dimension", "revise_dimension", "discard_dimension", "finish_design"]
    dimension: BenchmarkDimension | None = None
    dimension_id: str | None = None
    changes: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class GroundingAction(BaseModel):
    action: Literal["add_grounding", "defer_grounding", "block_grounding", "finish_grounding"]
    grounding: BenchmarkGrounding | None = None
    dimension_id: str | None = None
    rationale: str = ""


class AllocationDecision(BaseModel):
    action: Literal["set_allocation", "finish_allocation"]
    allocations: list[Allocation] = Field(default_factory=list)
    shortfall: int = Field(default=0, ge=0)
    rationale: str = ""


class ExecutorResult(BaseModel):
    action: Literal["item", "sample_failed", "allocation_exhausted"]
    item: BenchmarkItem | None = None
    source_id: str | None = None
    sample_id: str | None = None
    error: str | None = None


class VerificationResult(BaseModel):
    valid: bool
    status: Literal["verified", "accepted_with_warnings", "rejected", "failed"]
    reasons: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    control_action: Literal["none", "rerun", "replenish", "discard", "stop_allocation"] = "none"
    evidence: dict[str, Any] = Field(default_factory=dict)
