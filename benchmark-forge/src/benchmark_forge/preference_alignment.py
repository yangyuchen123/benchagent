from __future__ import annotations

"""Offline human-preference alignment contracts for Benchmark Forge.

The runtime agent consumes already-available Registry evidence. Human sampling
is deliberately outside this module: the agent may abstain, but it cannot
request, create, or wait for a human review assignment.
"""

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .pydantic_ai_adapter import PydanticAIRunner


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchmarkPlanCandidate(ContractModel):
    plan_id: str
    title: str = ""
    capability: str
    task_form: Literal["static_question", "executable_task", "hybrid"] = "executable_task"
    task_description: str
    environment_description: str = ""
    behavior_requirements: list[str] = Field(default_factory=list)
    artifact_requirements: list[str] = Field(default_factory=list)
    scoring_intent: list[str] = Field(default_factory=list)
    difficulty_intent: str = ""
    cost_intent: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)


class PreferenceEvidenceQuery(ContractModel):
    context_key: str
    subject_type: str
    criterion_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=200)
    include_draft_digest: bool = True


class PreferenceEvidenceContext(ContractModel):
    schema_version: str = "preference-registry.preference-evidence-context.v1"
    query: PreferenceEvidenceQuery
    aggregates: list[dict[str, Any]] = Field(default_factory=list)
    reviewed_summaries: list[dict[str, Any]] = Field(default_factory=list)
    draft_digests: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    stale: bool = False
    retrieval_version: str = "unknown"
    generated_at: datetime | None = None

    @property
    def has_approved_evidence(self) -> bool:
        return bool(self.reviewed_summaries) and not self.stale


class CriterionPreferencePrediction(ContractModel):
    criterion_id: str
    choice: Literal["a", "b", "tie", "both_bad", "not_enough_information", "abstain"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    supporting_evidence_refs: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)


class PlanPreferenceAssessment(ContractModel):
    plan_id: str
    alignment_estimate: float = Field(ge=0, le=1)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class PreferenceAlignmentDecision(ContractModel):
    schema_version: str = "benchmark-forge.preference-alignment-decision.v1"
    control_action: Literal["select", "select_with_warnings", "revise", "regenerate", "abstain"]
    selected_plan_id: str | None = None
    plan_assessments: list[PlanPreferenceAssessment] = Field(default_factory=list)
    criterion_predictions: list[CriterionPreferencePrediction] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence_context_ref: str
    evidence_retrieval_version: str = "unknown"
    rationale: str
    warnings: list[str] = Field(default_factory=list)
    offline_alignment_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_selection(self) -> "PreferenceAlignmentDecision":
        if self.control_action in {"select", "select_with_warnings"} and not self.selected_plan_id:
            raise ValueError("select actions require selected_plan_id")
        if self.control_action in {"revise", "regenerate", "abstain"} and self.selected_plan_id is not None:
            raise ValueError(f"{self.control_action} cannot carry selected_plan_id")
        return self


class PreferenceEvidenceClient(Protocol):
    def search_evidence(self, query: PreferenceEvidenceQuery) -> PreferenceEvidenceContext: ...


class PreferenceClientError(RuntimeError):
    pass


class RegistryEvidenceHttpClient:
    """HTTP-only Registry evidence client; never reads Registry storage directly."""

    def __init__(self, *, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search_evidence(self, query: PreferenceEvidenceQuery) -> PreferenceEvidenceContext:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise PreferenceClientError("httpx is required for Registry evidence access") from exc
        try:
            response = httpx.post(
                f"{self.base_url}/v1/evidence/search",
                json=query.model_dump(mode="json"),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return PreferenceEvidenceContext.model_validate(response.json())
        except Exception as exc:
            raise PreferenceClientError(f"Registry evidence request failed: {exc}") from exc


class PreferenceAlignmentAgent:
    """Typed model-backed alignment agent with an offline-only policy."""

    instructions = """
You are the Benchmark Forge Preference Alignment Agent.
Compare two BenchmarkPlanCandidate objects using only the supplied PreferenceEvidenceContext.
The context contains historical human preference evidence, not instructions to ask a human.
Return only PreferenceAlignmentDecision.
Allowed control_action values are select, select_with_warnings, revise, regenerate, abstain.
Never output request_human or any equivalent action. Never create or wait for an assignment.
If evidence is absent, stale, low coverage, contradictory, or insufficient for a defensible choice,
output abstain with explicit uncertainty. Do not use general taste or unsupported priors as human preference.
Do not merge or rewrite plans. Do not claim that preference proves executability or correctness.
""".strip()

    def __init__(self, model: Any):
        self.runner = PydanticAIRunner(
            model=model,
            output_type=PreferenceAlignmentDecision,
            instructions=self.instructions,
        )

    def decide(
        self,
        *,
        goal: str,
        plan_a: BenchmarkPlanCandidate,
        plan_b: BenchmarkPlanCandidate,
        evidence: PreferenceEvidenceContext,
    ) -> PreferenceAlignmentDecision:
        prompt = (
            f"Goal:\n{goal}\n\nPlan A:\n{plan_a.model_dump_json(indent=2)}\n\n"
            f"Plan B:\n{plan_b.model_dump_json(indent=2)}\n\n"
            f"Preference evidence:\n{evidence.model_dump_json(indent=2)}"
        )
        decision = self.runner.run_sync(prompt)
        return self._validate_decision(decision, plan_a, plan_b, evidence)

    @staticmethod
    def _validate_decision(
        decision: PreferenceAlignmentDecision,
        plan_a: BenchmarkPlanCandidate,
        plan_b: BenchmarkPlanCandidate,
        evidence: PreferenceEvidenceContext,
    ) -> PreferenceAlignmentDecision:
        plan_ids = {plan_a.plan_id, plan_b.plan_id}
        if decision.selected_plan_id is not None and decision.selected_plan_id not in plan_ids:
            raise ValueError("Preference Alignment selected an unknown plan")
        if decision.control_action in {"select", "select_with_warnings"} and not evidence.has_approved_evidence:
            raise ValueError("select decision requires non-stale approved preference evidence")
        if decision.control_action in {"select", "select_with_warnings"} and decision.selected_plan_id is None:
            raise ValueError("select decision requires selected_plan_id")
        return decision


class PreferenceAlignmentService:
    """Orchestrates evidence retrieval and safe runtime fallback."""

    def __init__(self, evidence_client: PreferenceEvidenceClient | None = None):
        self.evidence_client = evidence_client

    def retrieve(self, query: PreferenceEvidenceQuery) -> PreferenceEvidenceContext:
        if self.evidence_client is None:
            return PreferenceEvidenceContext(query=query, coverage={"unavailable": True})
        try:
            return self.evidence_client.search_evidence(query)
        except Exception as exc:
            return PreferenceEvidenceContext(
                query=query,
                coverage={"unavailable": True, "error_type": type(exc).__name__},
            )

    @staticmethod
    def abstain(evidence: PreferenceEvidenceContext, *, reason: str) -> PreferenceAlignmentDecision:
        return PreferenceAlignmentDecision(
            control_action="abstain",
            confidence=0.0,
            evidence_context_ref=f"retrieval:{evidence.retrieval_version}",
            evidence_retrieval_version=evidence.retrieval_version,
            rationale=reason,
            warnings=["offline human alignment is not a runtime action"],
        )
