"""Lightweight rubric integrity review for benchmark generation.

This is a generation-time construct check, not an attempt scorer.  It verifies
that a rubric still measures the requested capability and has not drifted into
an unrelated or impossible acceptance program.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from .environment_ir import EnvironmentIR
from .pydantic_ai_adapter import PydanticAIRunner


class RubricCriterionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str
    target_covered: bool
    scope: Literal["appropriate", "too_broad", "too_narrow"]
    direction: Literal["correct", "reversed", "unclear"]
    evidence_plausible: bool
    findings: list[str] = Field(default_factory=list)


class RubricIntegrityReview(BaseModel):
    """Review result used before materialization; no runtime evidence required."""
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "benchmark-forge.rubric-integrity-review.v1"
    verdict: Literal["pass", "revise", "reject"]
    target_alignment: Literal["aligned", "drifted", "unclear"]
    summary: str
    criterion_reviews: list[RubricCriterionReview] = Field(default_factory=list)
    global_findings: list[str] = Field(default_factory=list)
    repair_instructions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


def validate_rubric_integrity_review(ir: EnvironmentIR, review: RubricIntegrityReview) -> RubricIntegrityReview:
    """Reject malformed review output without judging rubric semantics itself."""
    expected = {criterion.criterion_id for criterion in ir.rubric.criteria}
    actual = [criterion.criterion_id for criterion in review.criterion_reviews]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(f"rubric review criteria mismatch: expected={sorted(expected)}, actual={sorted(actual)}")
    if review.verdict == "pass":
        if review.target_alignment != "aligned":
            raise ValueError("passing rubric review must be target aligned")
        if any(c.scope != "appropriate" or c.direction != "correct" or not c.target_covered or not c.evidence_plausible for c in review.criterion_reviews):
            raise ValueError("passing rubric review contains an unsafe criterion")
    if review.verdict == "reject" and not review.global_findings:
        raise ValueError("rejected rubric review must explain the global failure")
    return review


def review_rubric_integrity(*, model, ir: EnvironmentIR, timeout: float = 90.0) -> RubricIntegrityReview:
    """Ask one bounded reviewer to check construct alignment, not execution."""
    instructions = (
        "You are the Rubric Integrity Reviewer in benchmark generation. Review only whether the frozen rubric "
        "measures the requested benchmark target. Check target drift, scope too broad/too narrow, reversed "
        "direction, and whether requested evidence is plausibly observable. Do not design a scorer, inspect an "
        "attempt, invent hidden answers, add criteria, or request human intervention. Return one review for every "
        "criterion and only RubricIntegrityReview. Pass only when all criteria are aligned, appropriately scoped, "
        "correctly directed, and evidence-plausible. Use revise for fixable drift and reject for fundamentally "
        "opposite or unmeasurable criteria."
    )
    runner = PydanticAIRunner(model=model, output_type=RubricIntegrityReview, instructions=instructions, timeout=timeout, retries=0, label="role.rubric_integrity_review")
    prompt = (
        f"Task target and instruction:\n{ir.task_binding.instruction}\n\n"
        f"Task binding:\n{ir.task_binding.model_dump_json(indent=2)}\n\n"
        f"Frozen rubric:\n{ir.rubric.model_dump_json(indent=2)}\n\n"
        f"Registered evidence:\n{[e.model_dump(mode='json') for e in ir.evidence]}"
    )
    return validate_rubric_integrity_review(ir, runner.run_sync(prompt))


def validate_revised_rubric(ir: EnvironmentIR, rubric, review: RubricIntegrityReview):
    """Keep revision bounded to wording/bindings; scoring policy stays frozen."""
    from .environment_ir import IRRubric
    rubric = IRRubric.model_validate(rubric)
    before = {c.criterion_id: c for c in ir.rubric.criteria}
    after = {c.criterion_id: c for c in rubric.criteria}
    if set(before) != set(after) or len(rubric.criteria) != len(after):
        raise ValueError("rubric revision must preserve criterion IDs exactly")
    if rubric.pass_threshold != ir.rubric.pass_threshold or rubric.deterministic != ir.rubric.deterministic:
        raise ValueError("rubric revision may not change threshold or deterministic policy")
    for criterion_id, old in before.items():
        new = after[criterion_id]
        if new.weight != old.weight or new.minimum_score != old.minimum_score or new.critical_gate != old.critical_gate:
            raise ValueError(f"rubric revision may not change scoring policy for {criterion_id}")
    evidence_ids = {e.evidence_id for e in ir.evidence}
    artifact_ids = {a.artifact_id for a in ir.artifacts}
    state_ids = {s.state_id for s in ir.runtime_state}
    for criterion in rubric.criteria:
        if set(criterion.evidence_refs) - evidence_ids:
            raise ValueError(f"revised rubric {criterion.criterion_id} references unknown evidence")
        if set(criterion.artifact_refs) - artifact_ids:
            raise ValueError(f"revised rubric {criterion.criterion_id} references unknown artifacts")
        if set(criterion.state_refs) - state_ids:
            raise ValueError(f"revised rubric {criterion.criterion_id} references unknown states")
    return rubric


def revise_rubric_integrity(*, model, ir: EnvironmentIR, review: RubricIntegrityReview,
                             timeout: float = 90.0) -> EnvironmentIR:
    """Perform one bounded rubric-only rewrite after an integrity `revise` verdict."""
    from .environment_ir import IRRubric
    if review.verdict != "revise":
        raise ValueError("rubric revision requires a revise verdict")
    instructions = (
        "You are the bounded Rubric Revision Agent. Repair only the supplied IRRubric according to the integrity "
        "review. Preserve rubric_id, criterion IDs, criterion count, weights, minimum scores, critical gates, pass "
        "threshold, and deterministic policy exactly. You may only revise criterion descriptions and their declared "
        "evidence/artifact/state bindings using IDs already registered in EnvironmentIR. Do not add hidden answers, "
        "make requirements stricter than the public task, redesign the benchmark, write scorer code, or request human "
        "intervention. Return only IRRubric."
    )
    runner = PydanticAIRunner(model=model, output_type=IRRubric, instructions=instructions,
                              timeout=timeout, retries=0, label="role.rubric_integrity_revision")
    prompt = (
        f"Public task instruction:\n{ir.task_binding.instruction}\n\n"
        f"Current rubric:\n{ir.rubric.model_dump_json(indent=2)}\n\n"
        f"Registered evidence:\n{[e.model_dump(mode='json') for e in ir.evidence]}\n\n"
        f"Registered artifacts:\n{[a.model_dump(mode='json') for a in ir.artifacts]}\n\n"
        f"Registered runtime state:\n{[s.model_dump(mode='json') for s in ir.runtime_state]}\n\n"
        f"Integrity review and repair instructions:\n{review.model_dump_json(indent=2)}"
    )
    rubric = validate_revised_rubric(ir, runner.run_sync(prompt), review)
    draft = ir.model_copy(update={"rubric": rubric, "frozen": False, "ir_checksum": None, "frozen_at": None})
    # Re-validation happens through Pydantic reconstruction before a new checksum is frozen.
    revised = EnvironmentIR.model_validate(draft.model_dump(mode="json"))
    return revised.freeze()
