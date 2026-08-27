"""Typed contracts for agent-designed scorer semantics.

Scorer design belongs to the existing Verification & Control role.  The
Executor still owns Python implementation; Verification & Control defines what
must be observable, considers multiple legitimate implementations, and reviews
the produced scorer for construct validity before a pilot is run.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EvidenceSource = Literal[
    "artifact", "environment_state", "tool_trace", "agent_trajectory", "verifier"
]
EvidenceAuthority = Literal[
    "runtime_canonical", "runtime_correlated", "artifact_observed", "agent_self_report", "derived"
]


class ScorerImplementationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str
    dimension_name: str
    evidence_source: EvidenceSource
    authority: EvidenceAuthority
    strategy: str
    required_inputs: list[str] = Field(default_factory=list)
    observable_success: str
    observable_failure: str
    limitations: list[str] = Field(default_factory=list)
    fallback_rank: int = Field(default=0, ge=0)


class ScorerCalibrationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    description: str
    expected_behavior: str
    protects_against: list[str] = Field(default_factory=list)


class ScorerDesign(BaseModel):
    """Semantic design produced by Verification & Control before code exists."""

    model_config = ConfigDict(extra="forbid")

    design_version: str = "benchmark-forge.scorer-design.v1"
    scoring_objective: str
    public_contract_rules: list[str] = Field(default_factory=list)
    workspace_resolution_options: list[str] = Field(default_factory=list)
    evidence_precedence: list[EvidenceAuthority] = Field(default_factory=list)
    implementation_options: list[ScorerImplementationOption] = Field(default_factory=list)
    calibration_cases: list[ScorerCalibrationCase] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_real_alternatives(self) -> "ScorerDesign":
        by_dimension: dict[str, set[str]] = {}
        for option in self.implementation_options:
            by_dimension.setdefault(option.dimension_name, set()).add(option.option_id)
        if self.implementation_options and any(len(ids) < 2 for ids in by_dimension.values()):
            raise ValueError("each planned scoring dimension must have at least two implementation options")
        return self


class ScorerDimensionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_name: str
    covered: bool
    publicly_satisfiable: bool
    runtime_grounded: bool
    selected_option_ids: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)


class ScorerReview(BaseModel):
    """Semantic review of an implemented environment scorer."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "repair", "reject"]
    summary: str
    dimension_findings: list[ScorerDimensionFinding] = Field(default_factory=list)
    workspace_resolution_assessment: str = ""
    public_contract_assessment: str = ""
    evidence_authority_assessment: str = ""
    calibration_assessment: str = ""
    repair_instructions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
