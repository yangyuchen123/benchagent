from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agents import RoleAgents
from .domain import Benchmark, BenchmarkEvent, UserGoal
from .octagon import OctagonKnowledgeBase, RAGEnvironmentBlueprintProvider
from .orchestrator import BenchmarkOrchestrator, RunConfig
from .providers import SourceProvider
from .persistence import save_benchmark
from .staging import (CandidateCheck, EnvironmentCandidate, EnvironmentCandidateRegistry, merge_scaffold_bundles,
                      stage_generated_candidates, validate_agent_subject_contract, validate_contract_realizability, write_scaffold)
from .alignment_pipeline import PlanningAlignmentPipeline, PlanningAlignmentResult
from .environment_ir import (IRExpressivenessError, IRValidationError, IRComponentOutput,
                              component_output_from_bundle, link_component_outputs)
from .plan_adapter import benchmark_to_plan
from .preference_alignment import PreferenceEvidenceClient
from .pydantic_ai_adapter import telemetry_scope
from .planning_pair import DoublePlanningService
from .materialization_workflow import MaterializationPolicy, MaterializationWorkflow


def _telemetry_run(method):
    """Attach secret-free model-call telemetry when an artifact root is given."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        artifact_root = kwargs.get("artifact_root")
        if artifact_root is None:
            return method(self, *args, **kwargs)
        benchmark_id = kwargs.get("benchmark_id") or "benchmark"
        run_id = f"{benchmark_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        telemetry_path = Path(artifact_root) / "telemetry.jsonl"
        with telemetry_scope(telemetry_path, run_id=run_id):
            return method(self, *args, **kwargs)
    return wrapped


def materialize_candidates_with_scorer_control(
    *, agents: Any, benchmark: Benchmark, registry: EnvironmentCandidateRegistry,
    candidates: list[EnvironmentCandidate], max_scorer_repair_rounds: int = 1,
    require_frozen_ir: bool = False,
    enable_legacy_scorer_design: bool = False,
    max_component_repair_rounds: int = 2,
) -> None:
    """Run Executor implementation under Verification & Control scorer supervision."""
    materialize = getattr(agents, "materialize_environment", None)
    materialize_components = getattr(agents, "materialize_environment_components", None)
    design_scorer = getattr(agents, "design_scorer", None)
    review_scorer = getattr(agents, "review_environment_scorer", None)
    repair_scorer = getattr(agents, "repair_environment_scorer", None)
    generate_one_available = callable(getattr(agents, "generate_environment_component", None))
    if not callable(materialize) and not callable(materialize_components) and not generate_one_available:
        return
    for candidate in candidates:
        try:
            # Reload after IR compilation staging so a failed compiler cannot
            # accidentally fall through to the legacy whole-bundle materializer.
            candidate = registry.load(candidate.candidate_id)
            rubric_check = next((check for check in candidate.checks if check.check_id == "rubric_integrity"), None)
            if rubric_check is not None and rubric_check.status == "failed":
                benchmark.warnings.append(
                    f"component materialization skipped {candidate.candidate_id}: rubric integrity rejected"
                )
                continue
            if require_frozen_ir and candidate.environment_ir is None:
                registry.record_check(candidate.candidate_id, CandidateCheck(
                    check_id="environment_ir", stage="static", status="failed",
                    summary="component materialization blocked because Frozen IR is unavailable",
                    evidence_refs=[],
                ))
                benchmark.warnings.append(
                    f"component materialization skipped {candidate.candidate_id}: Frozen IR unavailable"
                )
                continue
            # Rubric is now generated from IR; the old scorer-design Agent is
            # an opt-in compatibility path because it creates a second,
            # unnecessarily heavy acceptance language.
            scorer_design = (design_scorer(candidate.item)
                             if enable_legacy_scorer_design and callable(design_scorer) else None)
            if scorer_design is not None:
                registry.record_scorer_design(candidate.candidate_id, scorer_design)
            # A frozen IR is authoritative for component generation. Keep the
            # legacy whole-bundle Executor only as a compatibility fallback for
            # candidates created before IR compilation was enabled.
            generate_one = getattr(agents, "generate_environment_component", None)
            repair_one = getattr(agents, "repair_environment_component", None)
            diagnose_failure = getattr(agents, "diagnose_materialization_failure", None)
            if candidate.environment_ir is not None and callable(generate_one):
                workflow = MaterializationWorkflow(
                    component_generator=lambda *, component_id, item, ir, dependency_outputs: generate_one(
                        item, ir, component_id, dependency_outputs, scorer_design,
                    ),
                    component_repairer=(
                        lambda *, component_id, item, ir, current, review, dependency_outputs: repair_one(
                            item, ir, component_id, current, review, scorer_design, dependency_outputs,
                        )
                    ) if callable(repair_one) else None,
                    diagnoser=(
                        lambda *, item, ir, outputs, observation: diagnose_failure(
                            item, ir, outputs, observation,
                        )
                    ) if callable(diagnose_failure) else None,
                    policy=MaterializationPolicy(
                        max_repairs_per_component=max_component_repair_rounds,
                    ),
                )
                workflow_root = registry.root / candidate.candidate_id / "materialization-workflow"
                bundle, workflow_report = workflow.run_components(
                    item=candidate.item, ir=candidate.environment_ir, output_root=workflow_root,
                )
                registry.record_check(candidate.candidate_id, CandidateCheck(
                    check_id="materialization_workflow", stage="scaffold",
                    status="passed" if workflow_report.status == "ready" else "failed",
                    summary=("automatic component generation/link/static validation completed"
                             if workflow_report.status == "ready" else workflow_report.stop_reason),
                    evidence_refs=[str(workflow_root / "workflow-report.json")],
                ))
                if bundle is None or workflow_report.status != "ready":
                    benchmark.warnings.append(
                        f"materialization workflow {candidate.candidate_id}: {workflow_report.stop_reason}"
                    )
                    continue
            elif candidate.environment_ir is not None and callable(materialize_components):
                bundle = materialize_components(candidate.item, candidate.environment_ir, scorer_design)
            elif callable(materialize):
                if scorer_design is not None:
                    bundle = materialize(candidate.item, scorer_design)
                else:
                    bundle = materialize(candidate.item)
            else:
                raise ValueError("no environment materializer configured")
            write_scaffold(registry, candidate.candidate_id, bundle)

            if scorer_design is not None and callable(review_scorer):
                review = review_scorer(candidate.item, bundle, scorer_design)
                registry.record_scorer_review(candidate.candidate_id, review)
                rounds = 0
                while (
                    review.verdict == "repair"
                    and callable(repair_scorer)
                    and rounds < max_scorer_repair_rounds
                ):
                    rounds += 1
                    if candidate.environment_ir is not None and callable(getattr(agents, "repair_environment_component", None)):
                        # Project only the scorer-owned files, repair them, then
                        # relink every component against the same frozen IR.
                        current_scorer = component_output_from_bundle(
                            candidate.environment_ir, bundle, "scorer"
                        )
                        repaired_scorer = agents.repair_environment_component(
                            candidate.item, candidate.environment_ir, "scorer",
                            current_scorer, review, scorer_design,
                        )
                        current_outputs = [
                            component_output_from_bundle(candidate.environment_ir, bundle, component_id)
                            for component_id in ("manifest", "runtime", "scorer", "tests")
                        ]
                        current_outputs = [
                            repaired_scorer if output.component_id == "scorer" else output
                            for output in current_outputs
                        ]
                        bundle = link_component_outputs(candidate.environment_ir, current_outputs)
                    elif callable(repair_scorer):
                        repair_bundle = repair_scorer(candidate.item, bundle, scorer_design, review)
                        bundle = merge_scaffold_bundles(bundle, repair_bundle)
                    else:
                        break
                    write_scaffold(registry, candidate.candidate_id, bundle)
                    review = review_scorer(candidate.item, bundle, scorer_design)
                    registry.record_scorer_review(candidate.candidate_id, review)
        except IRValidationError as exc:
            registry.record_check(candidate.candidate_id, CandidateCheck(
                check_id="ir_component_link", stage="scaffold", status="failed",
                summary=str(exc), evidence_refs=[],
            ))
            benchmark.warnings.append(f"environment component linking {candidate.candidate_id}: {exc}")
        except Exception as exc:
            benchmark.warnings.append(f"environment materialization {candidate.candidate_id}: {exc}")


def _review_candidate_rubric(*, agents: Any, registry: EnvironmentCandidateRegistry,
                              candidate: EnvironmentCandidate, ir: Any, benchmark: Benchmark) -> Any:
    """Review once, optionally perform one bounded rubric-only revision, then re-review."""
    reviewer = getattr(agents, "review_rubric_integrity", None)
    if not callable(reviewer):
        return ir
    try:
        review = reviewer(candidate.item, ir)
        registry.record_rubric_integrity_review(candidate.candidate_id, review)
        if review.verdict == "revise":
            reviser = getattr(agents, "revise_rubric_integrity", None)
            if callable(reviser):
                revised_ir = reviser(candidate.item, ir, review)
                registry.record_environment_ir(candidate.candidate_id, revised_ir)
                second_review = reviewer(candidate.item, revised_ir)
                registry.record_rubric_integrity_review(candidate.candidate_id, second_review)
                if second_review.verdict == "pass":
                    return revised_ir
                review = second_review
                ir = revised_ir
            benchmark.warnings.append(f"rubric integrity revise {candidate.candidate_id}: {review.summary}")
        elif review.verdict == "reject":
            benchmark.warnings.append(f"rubric integrity reject {candidate.candidate_id}: {review.summary}")
        return ir
    except Exception as exc:
        registry.record_check(candidate.candidate_id, CandidateCheck(
            check_id="rubric_integrity", stage="scoring", status="failed",
            summary=f"rubric integrity review failed: {exc}", evidence_refs=[],
        ))
        benchmark.warnings.append(f"rubric integrity review {candidate.candidate_id}: {exc}")
        return ir


@dataclass
class AlignedGenerationResult:
    alignment: PlanningAlignmentResult
    benchmark: Benchmark | None


@dataclass
class BenchmarkGenerationService:
    """System-facing natural-language benchmark generation entrypoint."""

    agents: RoleAgents
    knowledge_base: OctagonKnowledgeBase | None = None
    config: RunConfig = field(default_factory=RunConfig)
    max_scorer_repair_rounds: int = 1
    max_component_repair_rounds: int = 2
    ir_compiler: Any | None = None
    enable_legacy_scorer_design: bool = False

    @_telemetry_run
    def generate_with_alignment(
        self,
        goal: str,
        *,
        target_size: int = 1,
        providers: list[SourceProvider] | None = None,
        benchmark_id: str | None = None,
        artifact_root: str | Path | None = None,
        evidence_client: PreferenceEvidenceClient | None = None,
        decider: Any | None = None,
        model_id: str | None = None,
        knowledge_snapshot: str = "unknown",
        preference_context_key: str = "benchmark-plan",
        preference_subject_type: str = "benchmark-plan",
        similarity_threshold: float = 0.92,
        max_resamples: int = 2,
    ) -> "AlignedGenerationResult":
        """Generate two branches, align them, and materialize only the winner.

        This is intentionally an opt-in entrypoint. The legacy ``generate``
        method remains unchanged until callers migrate to this gate.
        """
        selected_providers = self._providers_for_goal(goal, target_size, providers)
        branches: dict[str, Benchmark] = {}
        branch_no = 0

        def generate_branch(_: str):
            nonlocal branch_no
            branch_no += 1
            branch_id = f"{benchmark_id or 'benchmark'}-alignment-{branch_no}"
            branch = BenchmarkOrchestrator(agents=self.agents, config=self.config).run(
                UserGoal(goal_id="natural-language", description=goal, target_size=target_size),
                selected_providers, benchmark_id=branch_id, artifact_root=None,
            )
            branches[branch_id] = branch
            return benchmark_to_plan(branch, plan_id=branch_id)

        planner = DoublePlanningService(
            generator=generate_branch,
            model_id=model_id or self.config.model_id,
            knowledge_snapshot=knowledge_snapshot,
            similarity_threshold=similarity_threshold,
            max_resamples=max_resamples,
        )
        pipeline = PlanningAlignmentPipeline(
            planner=planner, evidence_client=evidence_client, decider=decider,
        )
        result = pipeline.run(
            goal=goal, prompt=goal, context_key=preference_context_key,
            subject_type=preference_subject_type,
            pair_id=benchmark_id or "planning-pair",
        )
        if result.selected_plan is None:
            return AlignedGenerationResult(alignment=result, benchmark=None)

        branch = branches.get(result.selected_plan.plan_id)
        if branch is None:
            raise ValueError("selected plan has no generated benchmark branch")
        final_id = benchmark_id or branch.benchmark_id
        benchmark = branch.model_copy(update={"benchmark_id": final_id})
        benchmark.events.append(BenchmarkEvent(
            event_id=str(uuid4()),
            role="preference_alignment",
            event_type="preference_alignment_decided",
            payload={
                "control_action": result.decision.control_action,
                "selected_plan_id": result.selected_plan.plan_id,
                "evidence_context_ref": result.decision.evidence_context_ref,
                "pair_id": result.pair.pair_id,
            },
        ))
        benchmark.manifest["preference_alignment"] = {
            "pair_id": result.pair.pair_id,
            "selected_plan_id": result.selected_plan.plan_id,
            "decision": result.decision.model_dump(mode="json"),
        }
        if artifact_root is not None:
            self._materialize_selected(benchmark, artifact_root)
        return AlignedGenerationResult(alignment=result, benchmark=benchmark)

    def _providers_for_goal(
        self, goal: str, target_size: int, providers: list[SourceProvider] | None,
    ) -> list[SourceProvider]:
        selected = list(providers or [])
        if not selected:
            if self.knowledge_base is None:
                raise ValueError("natural-language environment generation requires a knowledge base or explicit providers")
            selected = [RAGEnvironmentBlueprintProvider(
                goal=goal, knowledge_base=self.knowledge_base, capacity_hint=target_size,
            )]
        return selected

    def _materialize_selected(self, benchmark: Benchmark, artifact_root: str | Path) -> None:
        root = Path(artifact_root)
        registry = EnvironmentCandidateRegistry(root / "environment-candidates")
        candidates = stage_generated_candidates(benchmark, registry)
        if self.ir_compiler is not None:
            for candidate in candidates:
                try:
                    contract = candidate.item.executable_task
                    if contract is None:
                        raise ValueError("candidate has no executable task contract")
                    scenario_errors = validate_contract_realizability(candidate.item)
                    if scenario_errors:
                        registry.clear_environment_ir(candidate.candidate_id)
                        registry.record_check(candidate.candidate_id, CandidateCheck(
                            check_id="scenario_contract", stage="static", status="failed",
                            summary="; ".join(scenario_errors), evidence_refs=[],
                        ))
                        benchmark.warnings.append(
                            f"Scenario contract {candidate.candidate_id}: {'; '.join(scenario_errors)}"
                        )
                        continue
                    registry.record_check(candidate.candidate_id, CandidateCheck(
                        check_id="scenario_contract", stage="static", status="passed",
                        summary="scenario dependencies have typed material/generator bindings",
                        evidence_refs=[],
                    ))
                    construct_errors = validate_agent_subject_contract(candidate.item)
                    if construct_errors:
                        registry.clear_environment_ir(candidate.candidate_id)
                        registry.record_check(candidate.candidate_id, CandidateCheck(
                            check_id="agent_subject_construct", stage="static", status="failed",
                            summary="; ".join(construct_errors), evidence_refs=[],
                        ))
                        benchmark.warnings.append(
                            f"Agent subject construct {candidate.candidate_id}: {'; '.join(construct_errors)}"
                        )
                        continue
                    registry.record_check(candidate.candidate_id, CandidateCheck(
                        check_id="agent_subject_construct", stage="static", status="passed",
                        summary="environment does not replace the evaluated Agent native capability",
                        evidence_refs=[],
                    ))
                    compiled_ir = self.ir_compiler.compile(contract)
                    registry.record_environment_ir(candidate.candidate_id, compiled_ir)
                    _review_candidate_rubric(agents=self.agents, registry=registry, candidate=candidate, ir=compiled_ir, benchmark=benchmark)
                except IRExpressivenessError as exc:
                    registry.record_check(candidate.candidate_id, CandidateCheck(
                        check_id="ir_expressiveness", stage="static", status="failed",
                        summary=str(exc), evidence_refs=[],
                    ))
                    benchmark.warnings.append(f"IR expressiveness {candidate.candidate_id}: {exc}")
                except Exception as exc:
                    # Staging creates a deterministic projection for legacy
                    # callers, but it is not a substitute for Compiler Agent
                    # output in the IR path. Clear it before materialization.
                    registry.clear_environment_ir(candidate.candidate_id)
                    registry.record_check(candidate.candidate_id, CandidateCheck(
                        check_id="ir_compilation", stage="static", status="failed",
                        summary=str(exc), evidence_refs=[],
                    ))
                    benchmark.warnings.append(f"IR compilation {candidate.candidate_id}: {exc}")
        materialize_candidates_with_scorer_control(
            agents=self.agents, benchmark=benchmark, registry=registry, candidates=candidates,
            max_scorer_repair_rounds=self.max_scorer_repair_rounds,
            require_frozen_ir=self.ir_compiler is not None,
            enable_legacy_scorer_design=self.enable_legacy_scorer_design,
            max_component_repair_rounds=self.max_component_repair_rounds,
        )
        benchmark.manifest["environment_candidates"] = [
            {"candidate_id": c.candidate_id, "status": registry.load(c.candidate_id).status.value, "registry_root": str(registry.root)}
            for c in candidates
        ]
        save_benchmark(root, benchmark)

    @_telemetry_run
    def generate(
        self,
        goal: str,
        *,
        target_size: int = 1,
        providers: list[SourceProvider] | None = None,
        benchmark_id: str | None = None,
        artifact_root: str | Path | None = None,
    ) -> Benchmark:
        selected = self._providers_for_goal(goal, target_size, providers)
        benchmark = BenchmarkOrchestrator(agents=self.agents, config=self.config).run(
            UserGoal(goal_id="natural-language", description=goal, target_size=target_size),
            selected,
            benchmark_id=benchmark_id,
            artifact_root=str(artifact_root) if artifact_root is not None else None,
        )
        if artifact_root is not None:
            registry = EnvironmentCandidateRegistry(Path(artifact_root) / "environment-candidates")
            candidates = stage_generated_candidates(benchmark, registry)
            if self.ir_compiler is not None:
                for candidate in candidates:
                    try:
                        contract = candidate.item.executable_task
                        if contract is None:
                            raise ValueError("candidate has no executable task contract")
                        scenario_errors = validate_contract_realizability(candidate.item)
                        if scenario_errors:
                            registry.clear_environment_ir(candidate.candidate_id)
                            registry.record_check(candidate.candidate_id, CandidateCheck(
                                check_id="scenario_contract", stage="static", status="failed",
                                summary="; ".join(scenario_errors), evidence_refs=[],
                            ))
                            benchmark.warnings.append(
                                f"Scenario contract {candidate.candidate_id}: {'; '.join(scenario_errors)}"
                            )
                            continue
                        registry.record_check(candidate.candidate_id, CandidateCheck(
                            check_id="scenario_contract", stage="static", status="passed",
                            summary="scenario dependencies have typed material/generator bindings",
                            evidence_refs=[],
                        ))
                        construct_errors = validate_agent_subject_contract(candidate.item)
                        if construct_errors:
                            registry.clear_environment_ir(candidate.candidate_id)
                            registry.record_check(candidate.candidate_id, CandidateCheck(
                                check_id="agent_subject_construct", stage="static", status="failed",
                                summary="; ".join(construct_errors), evidence_refs=[],
                            ))
                            benchmark.warnings.append(
                                f"Agent subject construct {candidate.candidate_id}: {'; '.join(construct_errors)}"
                            )
                            continue
                        registry.record_check(candidate.candidate_id, CandidateCheck(
                            check_id="agent_subject_construct", stage="static", status="passed",
                            summary="environment does not replace the evaluated Agent native capability",
                            evidence_refs=[],
                        ))
                        compiled_ir = self.ir_compiler.compile(contract)
                        registry.record_environment_ir(candidate.candidate_id, compiled_ir)
                        _review_candidate_rubric(agents=self.agents, registry=registry, candidate=candidate, ir=compiled_ir, benchmark=benchmark)
                    except IRExpressivenessError as exc:
                        registry.record_check(candidate.candidate_id, CandidateCheck(
                            check_id="ir_expressiveness", stage="static", status="failed",
                            summary=str(exc), evidence_refs=[],
                        ))
                        benchmark.warnings.append(f"IR expressiveness {candidate.candidate_id}: {exc}")
                    except Exception as exc:
                        registry.clear_environment_ir(candidate.candidate_id)
                        registry.record_check(candidate.candidate_id, CandidateCheck(
                            check_id="ir_compilation", stage="static", status="failed",
                            summary=str(exc), evidence_refs=[],
                        ))
                        benchmark.warnings.append(f"IR compilation {candidate.candidate_id}: {exc}")
            materialize_candidates_with_scorer_control(
                agents=self.agents, benchmark=benchmark, registry=registry, candidates=candidates,
                max_scorer_repair_rounds=self.max_scorer_repair_rounds,
                require_frozen_ir=self.ir_compiler is not None,
                enable_legacy_scorer_design=self.enable_legacy_scorer_design,
                max_component_repair_rounds=self.max_component_repair_rounds,
            )
            benchmark.manifest["environment_candidates"] = [
                {"candidate_id": c.candidate_id, "status": registry.load(c.candidate_id).status.value, "registry_root": str(registry.root)}
                for c in candidates
            ]
            save_benchmark(artifact_root, benchmark)
        return benchmark
