"""Benchmark Forge MVP.

The package keeps the original five roles: Design, Grounding, Allocation,
Executor, and Verification & Control. Preference Alignment is an auxiliary
pre-materialization role and uses offline human evidence only; it cannot request
or wait for human intervention. Pydantic models are the contract between roles;
the orchestration layer owns state transitions and event recording.
"""
from .domain import (
    Allocation,
    Benchmark,
    BenchmarkDimension,
    BenchmarkGrounding,
    BenchmarkItem,
    BenchmarkStatus,
    GroundingStatus,
    ItemStatus,
    SourceMode,
    UserGoal,
    ReplenishmentRequest,
    ArtifactRequirement, ContentReference, EnvironmentContract, ExecutableTaskContract,
    MaterialContract, ScenarioContract, ScoringContract, ScoringDimensionContract, ToolContract, WorkspaceContract,
    AgentCapabilityRequirement, CoordinationContract, DelegatedSubtaskContract,
    normalize_contract_bindings,
)
from .orchestrator import BenchmarkOrchestrator, RunConfig
from .environment_ir import (
    CORE_IR_FEATURES, CORE_IR_VERSION, IR_EXTENSION_VERSIONS, SUPPORTED_IR_FEATURES,
    EnvironmentIR, EnvironmentIRDraft, IRArtifact, IRComponentFile, IRMaterial, IRScenario,
    IRComponentOutput, IRComponentSpec, IREvidence, IRExpressivenessError, IRCoordinationGraph,
    IRCoordinationNode, IRRubric,
    IRRubricCriterion, IRStateField, IRTaskBinding, IRTool, IRValidationError, IRWorkspace,
    analyze_contract_expressiveness, component_output_from_bundle, default_component_specs, link_component_outputs,
    lower_contract_to_ir, normalize_ir_draft, validate_ir_contract_bindings,
)
from .service import AlignedGenerationResult, BenchmarkGenerationService
from .staging import (AgentEvalReportRef, CandidateCheck, CandidateStatus, EnvironmentCandidate,
                      EnvironmentCandidateRegistry, HumanApproval, PilotTrial, PromotionPolicy,
                      PromotionReadiness, stage_generated_candidates, merge_scaffold_bundles,
                      validate_contract_realizability)
from .pydantic_agents import PydanticAIRoleAgents
from .ir_compiler import EnvironmentIRCompilerAgent, IRCompilationError
from .component_agents import ComponentAgentFailure, generate_component_output, generate_component_outputs, repair_component_output
from .rubric_review import RubricCriterionReview, RubricIntegrityReview, review_rubric_integrity, validate_rubric_integrity_review, revise_rubric_integrity, validate_revised_rubric
from .scoring import (DeterministicCheck, EvidencePackage, EvidenceRecord, LLMRubricEvaluator,
                       RubricCriterionEvaluation, RubricEvaluation, normalize_evidence, validate_rubric_evaluation, rubric_checksum)
from .scorer_design import (ScorerCalibrationCase, ScorerDesign, ScorerDimensionFinding,
                            ScorerImplementationOption, ScorerReview)
from .graph import GraphDeps, build_graph, run_graph_sync
from .checkpoint import BenchmarkCheckpoint, load_checkpoint, save_checkpoint
from .pydantic_ai_adapter import openai_compatible_model
from .octagon import EnvironmentCatalog, EnvironmentDimension, EnvironmentProfile, KnowledgeChunk, OctagonKnowledgeBase, OctagonEnvironmentProvider, RAGEnvironmentBlueprintProvider, load_environment_profile

__all__ = [
    "EnvironmentCatalog", "EnvironmentDimension", "EnvironmentProfile", "KnowledgeChunk", "OctagonKnowledgeBase", "OctagonEnvironmentProvider", "RAGEnvironmentBlueprintProvider", "load_environment_profile",
    "ComponentAgentFailure", "generate_component_output", "generate_component_outputs", "repair_component_output", "CORE_IR_FEATURES", "CORE_IR_VERSION", "IR_EXTENSION_VERSIONS", "SUPPORTED_IR_FEATURES", "EnvironmentIR", "EnvironmentIRDraft", "IRArtifact", "IRComponentFile", "IRMaterial", "IRScenario", "IRComponentOutput", "IRComponentSpec", "IREvidence", "IRExpressivenessError", "IRCoordinationGraph", "IRCoordinationNode", "IRRubric", "IRRubricCriterion", "IRStateField", "IRTaskBinding", "IRTool", "IRValidationError", "IRWorkspace", "analyze_contract_expressiveness", "component_output_from_bundle", "default_component_specs", "link_component_outputs", "lower_contract_to_ir", "normalize_ir_draft", "validate_ir_contract_bindings", "EnvironmentIRCompilerAgent", "IRCompilationError",
    "Allocation", "Benchmark", "BenchmarkDimension", "BenchmarkGrounding",
    "BenchmarkItem", "BenchmarkStatus", "GroundingStatus", "ItemStatus",
    "SourceMode", "UserGoal", "ReplenishmentRequest", "ArtifactRequirement", "ContentReference",
    "EnvironmentContract", "ExecutableTaskContract", "MaterialContract", "ScenarioContract", "ScoringContract", "AlignedGenerationResult",
    "ScoringDimensionContract", "ToolContract", "WorkspaceContract", "BenchmarkGenerationService", "AgentEvalReportRef", "CandidateCheck",
    "CandidateStatus", "EnvironmentCandidate", "EnvironmentCandidateRegistry", "HumanApproval",
    "PilotTrial", "PromotionPolicy", "PromotionReadiness", "stage_generated_candidates", "merge_scaffold_bundles", "validate_contract_realizability", "AgentCapabilityRequirement",
    "CoordinationContract", "DelegatedSubtaskContract", "normalize_contract_bindings", "ScorerCalibrationCase", "ScorerDesign",
    "BenchmarkPlanCandidate", "CriterionPreferencePrediction", "PlanPreferenceAssessment",
    "PreferenceAlignmentAgent", "PreferenceAlignmentDecision", "PreferenceAlignmentService",
    "PreferenceClientError", "PreferenceEvidenceContext", "PreferenceEvidenceQuery",
    "RegistryEvidenceHttpClient", "BenchmarkPlanPair", "DoublePlanningService",
    "InsufficientPlanDiversity", "MaterializationGate", "PlanGenerator", "PlanProvenance",
    "PlanningPairError", "plan_similarity", "prompt_checksum", "PlanningAlignmentPipeline",
    "PlanningAlignmentResult", "benchmark_to_plan", "BundleTestResult", "FailureObservation", "FixedContractReplayWorkflow", "LocalPytestBackend", "MaterializationMetrics", "MaterializationPolicy", "MaterializationReport", "MaterializationWorkflow", "RepairPlan", "AgentCapacityLibrary",
    "CapacityBenchmarkSpec", "CapacityDefinition", "CapabilityId", "DEFAULT_CAPACITY_LIBRARY",
    "EvidenceSource", "build_default_capacity_library",
    "RubricCriterionReview", "RubricIntegrityReview", "review_rubric_integrity", "validate_rubric_integrity_review", "revise_rubric_integrity", "validate_revised_rubric", "DeterministicCheck", "EvidencePackage", "EvidenceRecord", "LLMRubricEvaluator", "RubricCriterionEvaluation", "RubricEvaluation", "normalize_evidence", "validate_rubric_evaluation", "rubric_checksum", "ScorerDimensionFinding", "ScorerImplementationOption", "ScorerReview", "BenchmarkOrchestrator", "RunConfig", "PydanticAIRoleAgents", "GraphDeps", "build_graph", "run_graph_sync", "BenchmarkCheckpoint", "load_checkpoint", "save_checkpoint", "openai_compatible_model",
]
from .materialization_workflow import (
    BundleTestResult, FailureObservation, FixedContractReplayWorkflow,
    LocalPytestBackend, MaterializationMetrics, MaterializationPolicy,
    MaterializationReport, MaterializationWorkflow, RepairPlan,
)

from .preference_alignment import (
    BenchmarkPlanCandidate,
    CriterionPreferencePrediction,
    PlanPreferenceAssessment,
    PreferenceAlignmentAgent,
    PreferenceAlignmentDecision,
    PreferenceAlignmentService,
    PreferenceClientError,
    PreferenceEvidenceContext,
    PreferenceEvidenceQuery,
    RegistryEvidenceHttpClient,
)

from .planning_pair import (
    BenchmarkPlanPair, DoublePlanningService, InsufficientPlanDiversity, MaterializationGate,
    PlanGenerator, PlanProvenance, PlanningPairError, plan_similarity, prompt_checksum,
)

from .alignment_pipeline import PlanningAlignmentPipeline, PlanningAlignmentResult

from .plan_adapter import benchmark_to_plan

from .capacity_library import (
    AgentCapacityLibrary, CapacityBenchmarkSpec, CapacityDefinition, CapabilityId,
    DEFAULT_CAPACITY_LIBRARY, EvidenceSource, build_default_capacity_library,
)
