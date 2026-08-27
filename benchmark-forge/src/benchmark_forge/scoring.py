from __future__ import annotations

"""Evidence-first scoring primitives.

Runtime/eval-system produces facts.  This module normalizes those facts into a
stable :class:`EvidencePackage`; one generic LLM evaluator then judges a frozen
IR rubric.  Benchmark-specific code is an adapter, not a second acceptance
state machine.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .environment_ir import EnvironmentIR
from .pydantic_ai_adapter import PydanticAIRunner


class ScoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRecord(ScoringModel):
    evidence_id: str
    source_type: Literal["artifact", "tool_trace", "environment_state", "agent_trajectory", "resource_usage", "verifier"]
    authority: Literal["environment_runtime", "workspace_artifact", "system_verifier", "agent_report"]
    observed: bool = True
    payload: Any = None
    provenance_refs: list[str] = Field(default_factory=list)


class DeterministicCheck(ScoringModel):
    check_id: str
    status: Literal["passed", "failed", "not_observed"]
    detail: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class EvidencePackage(ScoringModel):
    schema_version: str = "benchmark-forge.evidence-package.v1"
    attempt_id: str
    ir_checksum: str
    rubric_id: str
    rubric_checksum: str
    # IR logical evidence_id -> concrete package evidence IDs.  The evaluator
    # never guesses how ``evidence_artifact`` maps to an artifact record.
    evidence_bindings: dict[str, list[str]] = Field(default_factory=dict)
    artifacts: list[EvidenceRecord] = Field(default_factory=list)
    tool_events: list[EvidenceRecord] = Field(default_factory=list)
    state_transitions: list[EvidenceRecord] = Field(default_factory=list)
    agent_events: list[EvidenceRecord] = Field(default_factory=list)
    resource_usage: list[EvidenceRecord] = Field(default_factory=list)
    verifier_evidence: list[EvidenceRecord] = Field(default_factory=list)
    deterministic_checks: list[DeterministicCheck] = Field(default_factory=list)

    @property
    def records(self) -> list[EvidenceRecord]:
        return [record for group in (
            self.artifacts, self.tool_events, self.state_transitions,
            self.agent_events, self.resource_usage, self.verifier_evidence,
        ) for record in group]

    def concrete_ids(self) -> set[str]:
        return {record.evidence_id for record in self.records}

    def allowed_refs(self, logical_refs: list[str]) -> set[str]:
        return {concrete for logical in logical_refs for concrete in self.evidence_bindings.get(logical, [])}


class RubricCriterionEvaluation(ScoringModel):
    criterion_id: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    verdict: Literal["pass", "fail", "insufficient_evidence"]
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str
    uncertainties: list[str] = Field(default_factory=list)


class RubricEvaluation(ScoringModel):
    schema_version: str = "benchmark-forge.rubric-evaluation.v1"
    attempt_id: str
    ir_checksum: str
    rubric_id: str
    evaluator_model: str
    criteria: list[RubricCriterionEvaluation]
    overall_score: float = Field(ge=0, le=100)
    overall_verdict: Literal["pass", "fail", "insufficient_evidence"]
    evaluator_uncertainties: list[str] = Field(default_factory=list)


def _checksum(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rubric_checksum(ir: EnvironmentIR) -> str:
    return _checksum(ir.rubric.model_dump(mode="json"))


def _source_records(prefix: str, source_type: Any, authority: Any, values: list[dict[str, Any]]) -> list[EvidenceRecord]:
    return [EvidenceRecord(
        evidence_id=f"{prefix}:{index}", source_type=source_type, authority=authority,
        payload=value, provenance_refs=[f"{prefix}:{index}"],
    ) for index, value in enumerate(values)]


def normalize_evidence(
    ir: EnvironmentIR,
    *, attempt_id: str,
    artifacts: dict[str, Any] | None = None,
    tool_events: list[dict[str, Any]] | None = None,
    state_transitions: list[dict[str, Any]] | None = None,
    agent_events: list[dict[str, Any]] | None = None,
    resource_usage: dict[str, Any] | None = None,
    verifier_evidence: list[dict[str, Any]] | None = None,
) -> EvidencePackage:
    """Normalize already-collected eval-system observations without inference.

    ``artifacts`` keys are canonical IR artifact IDs. Missing required artifacts
    become deterministic failures, while absent optional artifacts are marked as
    not observed. Logical IR evidence IDs are explicitly bound to concrete IDs.
    """
    artifact_values = artifacts or {}
    known_artifacts = {item.artifact_id: item for item in ir.artifacts}
    artifact_records: list[EvidenceRecord] = []
    checks: list[DeterministicCheck] = []
    for artifact_id, artifact in known_artifacts.items():
        exists = artifact_id in artifact_values and artifact_values[artifact_id] is not None
        evidence_id = f"artifact:{artifact_id}"
        artifact_records.append(EvidenceRecord(
            evidence_id=evidence_id, source_type="artifact", authority="workspace_artifact",
            observed=exists, payload=artifact_values.get(artifact_id), provenance_refs=[artifact.path],
        ))
        checks.append(DeterministicCheck(
            check_id=f"artifact_present:{artifact_id}",
            status="passed" if exists else ("failed" if artifact.required else "not_observed"),
            detail=f"canonical artifact {artifact_id} at {artifact.path}", evidence_refs=[evidence_id],
        ))

    raw_tool_events = tool_events or []
    tool_records = _source_records("tool_event", "tool_trace", "environment_runtime", raw_tool_events)
    # A generic ``tool_id/status`` report is not enough to prove that the
    # runtime emitted the canonical trace event declared by the IR. Keep this
    # as an explicit diagnostic rather than allowing the LLM to infer it.
    for tool in ir.tools:
        matching = [event for event in raw_tool_events if isinstance(event, Mapping) and event.get("tool_id") == tool.tool_id]
        has_canonical_event = any(
            event.get("event_type") == tool.trace_event or event.get("trace_event") == tool.trace_event
            for event in matching
        )
        if matching and tool.trace_event and not has_canonical_event:
            checks.append(DeterministicCheck(
                check_id=f"tool_trace_canonical:{tool.tool_id}", status="not_observed",
                detail=f"tool calls exist but canonical trace event {tool.trace_event!r} was not supplied",
            ))
    state_records = _source_records("state_transition", "environment_state", "environment_runtime", state_transitions or [])
    agent_records = _source_records("agent_event", "agent_trajectory", "agent_report", agent_events or [])
    verifier_records = _source_records("verifier", "verifier", "system_verifier", verifier_evidence or [])
    resource_records = [] if resource_usage is None else [EvidenceRecord(
        evidence_id="resource_usage:summary", source_type="resource_usage", authority="environment_runtime",
        payload=resource_usage, provenance_refs=["resource_usage:summary"],
    )]
    records = artifact_records + tool_records + state_records + agent_records + resource_records + verifier_records
    concrete = {record.evidence_id for record in records}
    bindings: dict[str, list[str]] = {}
    for evidence in ir.evidence:
        prefix = {
            "artifact": "artifact:", "tool_trace": "tool_event:",
            "environment_state": "state_transition:", "agent_trajectory": "agent_event:",
            "verifier": "verifier:",
        }[evidence.source_type]
        ids = [record.evidence_id for record in records if record.evidence_id.startswith(prefix)]
        bindings[evidence.evidence_id] = ids
        for criterion in ir.rubric.criteria:
            if evidence.evidence_id in criterion.evidence_refs and not ids:
                checks.append(DeterministicCheck(
                    check_id=f"evidence_registered:{criterion.criterion_id}:{evidence.evidence_id}",
                    status="not_observed", detail=f"rubric evidence {evidence.evidence_id} was not supplied",
                ))
    # Ensure package construction itself catches accidental adapter references.
    unknown_binding_ids = set(bindings) - {item.evidence_id for item in ir.evidence}
    if unknown_binding_ids:
        raise ValueError(f"unknown logical evidence bindings: {sorted(unknown_binding_ids)}")
    return EvidencePackage(
        attempt_id=attempt_id, ir_checksum=ir.ir_checksum or ir.semantic_checksum(),
        rubric_id=ir.rubric.rubric_id, rubric_checksum=rubric_checksum(ir), evidence_bindings=bindings,
        artifacts=artifact_records, tool_events=tool_records, state_transitions=state_records,
        agent_events=agent_records, resource_usage=resource_records, verifier_evidence=verifier_records,
        deterministic_checks=checks,
    )


def _artifact_value(sample: Any, artifact: Any) -> Any:
    """Read one eval-system artifact using its public Artifacts interface."""
    artifacts = getattr(sample, "artifacts", None)
    if artifacts is None:
        return None
    candidate_paths = [artifact.path, artifact.path.removeprefix("artifact://"), artifact.path.lstrip("/")]
    for path in candidate_paths:
        try:
            entry = artifacts.find(path)
            if entry is not None and getattr(entry, "type", "file") == "file":
                host_path = Path(entry.host_path)
                if host_path.is_file():
                    raw = host_path.read_text(encoding="utf-8")
                    if (artifact.media_type or "").endswith("json") or host_path.suffix == ".json":
                        try:
                            return json.loads(raw)
                        except json.JSONDecodeError:
                            return raw
                    return raw
        except (OSError, UnicodeDecodeError, AttributeError):
            continue
    return None


def validate_rubric_evaluation(ir: EnvironmentIR, package: EvidencePackage, result: RubricEvaluation) -> RubricEvaluation:
    """Deterministically validate and recompute an evaluator response."""
    if not ir.frozen:
        raise ValueError("rubric evaluation requires frozen IR")
    checksum = ir.semantic_checksum()
    if ir.ir_checksum != checksum:
        raise ValueError("Frozen IR checksum does not match semantic content")
    if package.ir_checksum != checksum:
        raise ValueError("evidence package IR checksum does not match Frozen IR")
    if package.attempt_id != result.attempt_id:
        raise ValueError("evaluator attempt_id does not match EvidencePackage")
    if result.ir_checksum != checksum or result.rubric_id != ir.rubric.rubric_id:
        raise ValueError("evaluator rubric identity does not match Frozen IR")
    if package.rubric_id != ir.rubric.rubric_id or package.rubric_checksum != rubric_checksum(ir):
        raise ValueError("EvidencePackage rubric checksum does not match Frozen IR")
    expected = {c.criterion_id for c in ir.rubric.criteria}
    actual = [c.criterion_id for c in result.criteria]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(f"evaluator criteria mismatch: expected={sorted(expected)}, actual={sorted(actual)}")
    concrete = package.concrete_ids()
    logical = {e.evidence_id for e in ir.evidence}
    for criterion in result.criteria:
        spec = next(c for c in ir.rubric.criteria if c.criterion_id == criterion.criterion_id)
        allowed = package.allowed_refs(spec.evidence_refs)
        invalid = set(criterion.evidence_refs) - concrete
        disallowed = set(criterion.evidence_refs) - allowed
        if invalid:
            raise ValueError(f"evaluator cited unknown evidence: {sorted(invalid)}")
        if disallowed:
            raise ValueError(f"evaluator cited evidence outside criterion binding: {sorted(disallowed)}")
        if criterion.verdict == "insufficient_evidence" and criterion.score > 0:
            raise ValueError(f"insufficient_evidence criterion {criterion.criterion_id} must have score 0")
        if criterion.verdict == "pass" and not criterion.evidence_refs:
            raise ValueError(f"passed criterion {criterion.criterion_id} must cite evidence")
        if criterion.verdict == "pass" and criterion.score < (spec.minimum_score or 0):
            raise ValueError(f"criterion {criterion.criterion_id} is below minimum_score")
    weights = [c.weight for c in ir.rubric.criteria]
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("rubric must have positive total weight")
    by_id = {c.criterion_id: c for c in result.criteria}
    recomputed = sum(by_id[c.criterion_id].score * c.weight for c in ir.rubric.criteria) / total_weight
    if abs(result.overall_score - recomputed) > 0.01:
        raise ValueError(f"overall_score mismatch: expected={recomputed:.4f}, actual={result.overall_score:.4f}")
    critical_failed = any(c.critical_gate and by_id[c.criterion_id].verdict != "pass" for c in ir.rubric.criteria)
    insufficient = any(c.verdict == "insufficient_evidence" for c in result.criteria)
    expected_verdict = "insufficient_evidence" if insufficient else ("fail" if critical_failed or recomputed < ir.rubric.pass_threshold else "pass")
    if result.overall_verdict != expected_verdict:
        raise ValueError(f"overall_verdict mismatch: expected={expected_verdict}, actual={result.overall_verdict}")
    return result


@dataclass
class LLMRubricEvaluator:
    """Generic evaluator; it judges evidence but never designs the rubric."""
    model: Any
    timeout: float = 90.0
    evaluator_model: str = "unknown"
    instructions: str = (
        "You are the LLM Rubric Evaluator. Evaluate only the frozen rubric and supplied EvidencePackage. "
        "Do not invent criteria, weights, thresholds, facts, or missing evidence. Cite only concrete evidence IDs "
        "allowed by evidence_bindings. If a criterion cannot be established from authoritative evidence, return "
        "insufficient_evidence. Do not treat agent self-report as runtime truth. Return only RubricEvaluation."
    )

    def evaluate(self, ir: EnvironmentIR, package: EvidencePackage) -> RubricEvaluation:
        if not ir.frozen:
            raise ValueError("rubric evaluation requires frozen IR")
        current_checksum = ir.semantic_checksum()
        if ir.ir_checksum != current_checksum:
            raise ValueError("Frozen IR checksum does not match semantic content")
        if package.ir_checksum != current_checksum:
            raise ValueError("evidence package IR checksum does not match Frozen IR")
        runner = PydanticAIRunner(model=self.model, output_type=RubricEvaluation, instructions=self.instructions, timeout=self.timeout, retries=0, label="rubric_evaluator")
        prompt = (f"Frozen rubric:\n{json.dumps(ir.rubric.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n\n"
                  f"EvidencePackage:\n{package.model_dump_json(indent=2)}\n\n"
                  "Evaluate every criterion exactly once. Return weighted overall_score and verdict.")
        result = runner.run_sync(prompt)
        return validate_rubric_evaluation(ir, package, result)
