from __future__ import annotations

"""Composable double-planning → offline evidence alignment pipeline."""

from dataclasses import dataclass
from typing import Protocol

from .planning_pair import BenchmarkPlanPair, DoublePlanningService, MaterializationGate, PlanningPairError
from .preference_alignment import (
    BenchmarkPlanCandidate,
    PreferenceAlignmentDecision,
    PreferenceEvidenceClient,
    PreferenceEvidenceContext,
    PreferenceEvidenceQuery,
)


class AlignmentDecider(Protocol):
    def decide(self, *, goal: str, plan_a: BenchmarkPlanCandidate, plan_b: BenchmarkPlanCandidate,
               evidence: PreferenceEvidenceContext) -> PreferenceAlignmentDecision: ...


@dataclass
class PlanningAlignmentResult:
    pair: BenchmarkPlanPair
    evidence: PreferenceEvidenceContext
    decision: PreferenceAlignmentDecision
    selected_plan: BenchmarkPlanCandidate | None = None


class PlanningAlignmentPipeline:
    """Run alignment without introducing a runtime human-intervention path."""

    def __init__(
        self,
        *,
        planner: DoublePlanningService,
        evidence_client: PreferenceEvidenceClient | None,
        decider: AlignmentDecider | None,
    ):
        self.planner = planner
        self.evidence_client = evidence_client
        self.decider = decider

    def run(
        self, *, goal: str, prompt: str, context_key: str, subject_type: str,
        pair_id: str = "planning-pair",
    ) -> PlanningAlignmentResult:
        pair = self.planner.generate_pair(prompt, pair_id=pair_id)
        evidence = self._evidence(context_key=context_key, subject_type=subject_type)
        if pair.status != "ready":
            decision = PreferenceAlignmentDecision(
                control_action="regenerate",
                confidence=0.0,
                evidence_context_ref=f"planning-pair:{pair.pair_id}",
                evidence_retrieval_version=evidence.retrieval_version,
                rationale="The two independent plans did not reach sufficient diversity.",
                warnings=["bounded same-prompt resampling exhausted"],
            )
        elif self.decider is None:
            decision = PreferenceAlignmentDecision(
                control_action="abstain",
                confidence=0.0,
                evidence_context_ref=f"retrieval:{evidence.retrieval_version}",
                evidence_retrieval_version=evidence.retrieval_version,
                rationale="Preference Alignment decider is unavailable.",
                warnings=["offline human alignment is not a runtime action"],
            )
        else:
            decision = self.decider.decide(
                goal=goal, plan_a=pair.plan_a, plan_b=pair.plan_b, evidence=evidence
            )
        selected = None
        if decision.control_action in {"select", "select_with_warnings"}:
            selected = MaterializationGate.authorize(pair, decision)
        return PlanningAlignmentResult(
            pair=pair, evidence=evidence, decision=decision, selected_plan=selected
        )

    def _evidence(self, *, context_key: str, subject_type: str) -> PreferenceEvidenceContext:
        query = PreferenceEvidenceQuery(context_key=context_key, subject_type=subject_type)
        if self.evidence_client is None:
            return PreferenceEvidenceContext(query=query, coverage={"unavailable": True})
        try:
            return self.evidence_client.search_evidence(query)
        except Exception as exc:
            return PreferenceEvidenceContext(
                query=query,
                coverage={"unavailable": True, "error_type": type(exc).__name__},
            )
