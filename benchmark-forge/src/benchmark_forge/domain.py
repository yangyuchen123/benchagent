from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
import hashlib
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_environment_id(value: str) -> str:
    raw = str(value).strip().lower()
    ascii_value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        slug = f"generated-environment-{hashlib.sha256(raw.encode()).hexdigest()[:8]}"
    if not slug[0].isalnum():
        slug = "env-" + slug
    return slug[:96].rstrip("-")


class UserGoal(BaseModel):
    goal_id: str
    description: str
    target_size: int = Field(ge=1)
    require_full_target: bool = False


class SourceMode(StrEnum):
    EXISTING_DATASET = "existing_dataset"
    EXISTING_ENVIRONMENT = "existing_environment"
    GENERATED_ENVIRONMENT = "generated_environment"
    SYNTHETIC = "synthetic"
    HYBRID = "hybrid"
    PENDING = "pending"
    BLOCKED = "blocked"


class GroundingStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    PROVISIONAL = "provisional"
    PENDING = "pending"
    BLOCKED = "blocked"
    REJECTED = "rejected"


class BenchmarkStatus(StrEnum):
    DRAFT = "draft"
    DESIGNED = "designed"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    COMPLETED = "completed"
    FAILED = "failed"


class ItemStatus(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
    REJECTED = "rejected"
    FAILED = "failed"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchmarkDimension(Model):
    id: str
    name: str
    description: str
    capability: str = "general_reasoning"
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    answer_type: Literal["multiple_choice", "open_ended", "true_false", "artifact", "agent_trajectory"] = "open_ended"
    task_form: Literal["static_question", "executable_task", "hybrid"] = "executable_task"
    constraints: dict[str, Any] = Field(default_factory=dict)
    status: Literal["proposed", "active", "pending", "blocked", "discarded"] = "proposed"


class TransformStep(Model):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)


class TransformPlan(Model):
    steps: list[TransformStep] = Field(default_factory=list)
    rationale: str = ""


class GroundingScores(Model):
    alignment: float = 0.0
    robustness: float = 0.0
    signal_preservation: float = 0.0
    answerability: float = 0.0
    uniqueness: float = 0.0


class BenchmarkGrounding(Model):
    dimension_id: str
    source_mode: SourceMode
    source_id: str | None = None
    plan: TransformPlan = Field(default_factory=TransformPlan)
    estimated_capacity: int = 0
    executable_capacity: int = 0
    realization_capacity: int = 0
    status: GroundingStatus
    scores: GroundingScores = Field(default_factory=GroundingScores)
    reasons: dict[str, str] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def generatable(self) -> bool:
        return self.source_mode == SourceMode.GENERATED_ENVIRONMENT and self.realization_capacity > 0

    @property
    def executable(self) -> bool:
        return self.status in {
            GroundingStatus.READY,
            GroundingStatus.PARTIAL,
            GroundingStatus.PROVISIONAL,
        } and self.executable_capacity > 0


class Allocation(Model):
    dimension_id: str
    source_mode: SourceMode
    source_id: str | None = None
    executable_quota: int = Field(default=0, ge=0)
    realization_quota: int = Field(default=0, ge=0)
    deferred_quota: int = Field(default=0, ge=0)
    replenishment_strategy: str | None = None

    @property
    def total_quota(self) -> int:
        return self.executable_quota + self.realization_quota + self.deferred_quota


class SourceRef(Model):
    source_mode: SourceMode
    source_id: str
    sample_id: str
    fields: list[str] = Field(default_factory=list)


class ContentReference(Model):
    type: Literal["path", "git", "registry", "generated", "knowledge"]
    ref: str
    checksum: str | None = None
    version: str | None = None


class ToolContract(Model):
    name: str
    description: str = ""
    ownership: Literal["benchmark_environment", "agent_runtime", "evaluation_system"] = "benchmark_environment"
    interface: Literal["mcp", "builtin", "cli", "http", "python", "other"] = "other"
    entrypoint: dict[str, Any] = Field(default_factory=dict)
    side_effects: list[str] = Field(default_factory=list)


class MaterialContract(Model):
    """A versionable input binding consumed by the generated environment."""

    material_id: str = ""
    source: ContentReference
    target: str
    read_only: bool = True
    required: bool = True
    visibility: Literal["agent", "evaluation_system"] = "agent"
    description: str = ""
    schema_ref: str | None = None
    minimum_items: int | None = Field(default=None, ge=0)
    collection_key: str | None = None

    @model_validator(mode="after")
    def normalize_material(self) -> "MaterialContract":
        target_path = Path(self.target)
        if target_path.is_absolute() or ".." in target_path.parts:
            raise ValueError("material target must be a safe relative path")
        if not self.material_id:
            raw = target_path.stem or self.source.ref.rsplit("/", 1)[-1]
            self.material_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw).strip("_").lower() or "material"
        if not re.match(r"^[a-z][a-z0-9_-]*$", self.material_id):
            raise ValueError("material_id must match ^[a-z][a-z0-9_-]*$")
        return self


class ScenarioContract(Model):
    """Minimum task-world conditions required to exercise the target construct."""

    data_dependent: bool = False
    material_refs: list[str] = Field(default_factory=list)
    runtime_generator_ref: ContentReference | None = None
    evaluation_injection_ref: ContentReference | None = None
    allow_empty: bool = False
    minimum_items: int = Field(default=1, ge=0)
    required_case_tags: list[str] = Field(default_factory=list)
    case_tag_field: str | None = None

    @model_validator(mode="after")
    def validate_scenario(self) -> "ScenarioContract":
        if self.allow_empty and self.minimum_items > 0:
            raise ValueError("allow_empty scenario must use minimum_items=0")
        if self.required_case_tags and not self.case_tag_field:
            raise ValueError("required_case_tags requires case_tag_field")
        return self


class WorkspaceContract(Model):
    isolated: bool = True
    network: Literal["forbidden", "restricted", "allowed"] = "forbidden"
    writable_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=lambda: ["private", "scorer", "expected"])


class EnvironmentContract(Model):
    protocol: str = "octagon.env.v1"
    environment_id: str

    @field_validator("environment_id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> str:
        return normalize_environment_id(str(value))
    environment_type: str = "local"
    implementation: ContentReference | None = None
    prerequisites: dict[str, Any] = Field(default_factory=dict)
    entrypoints: dict[str, Any] = Field(default_factory=dict)
    tools: list[ToolContract] = Field(default_factory=list)
    materials: list[MaterialContract] = Field(default_factory=list)
    scenario: ScenarioContract | None = None
    workspace: WorkspaceContract = Field(default_factory=WorkspaceContract)
    timeout_seconds: int = Field(default=600, ge=1)
    maturity: Literal["existing", "adapted", "generated_contract", "pending"] = "generated_contract"


class ArtifactRequirement(Model):
    path: str
    description: str
    required: bool = True
    media_type: str | None = None
    schema_path: str | None = None
    schema_def: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_artifact_schema_binding(self) -> "ArtifactRequirement":
        for value, label in ((self.path, "artifact path"), (self.schema_path, "artifact schema_path")):
            if value is None:
                continue
            candidate = Path(value)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"{label} must be a safe relative path")
        return self


class ScoringDimensionContract(Model):
    name: str
    weight: float = Field(ge=0)
    description: str
    minimum_score: float | None = Field(default=None, ge=0, le=100)
    critical_gate: bool = False
    evidence_sources: list[Literal["artifact", "environment_state", "tool_trace", "agent_trajectory", "verifier"]] = Field(default_factory=list)


class ScoringContract(Model):
    dimensions: list[ScoringDimensionContract] = Field(default_factory=list)
    pass_threshold: float | None = Field(default=None, ge=0, le=100)
    scorer_ref: ContentReference | None = None
    deterministic: bool = True


class AgentCapabilityRequirement(Model):
    name: Literal["subagent_spawn", "subagent_message", "subagent_wait", "subagent_trace", "multi_turn", "tool_use", "workspace", "memory", "context_management"]
    required: bool = True
    description: str = ""
    on_missing: Literal["block", "diagnostic_zero", "allow_fallback"] = "block"


class DelegatedSubtaskContract(Model):
    subtask_id: str
    objective: str
    depends_on: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    acceptance_checks: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(default_factory=list)


class CoordinationContract(Model):
    mode: Literal["subagent_delegation"] = "subagent_delegation"
    subtasks: list[DelegatedSubtaskContract]
    min_subagents: int = Field(default=1, ge=1)
    max_subagents: int | None = Field(default=None, ge=1)
    require_distinct_assignment: bool = True
    require_parallel_independent_work: bool = False
    require_agent_attribution: bool = True
    require_acceptance_evidence: bool = True
    repair_budget: int = Field(default=1, ge=0)


class ExecutableTaskContract(Model):
    task_id: str
    instruction: str
    context: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    agent_capabilities: list[AgentCapabilityRequirement] = Field(default_factory=list)
    coordination: CoordinationContract | None = None
    environment: EnvironmentContract
    artifacts: list[ArtifactRequirement] = Field(default_factory=list)
    scoring: ScoringContract
    observation_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_execution_contract(self) -> "ExecutableTaskContract":
        if not self.environment.tools and not self.environment.entrypoints and not self.environment.materials:
            raise ValueError("executable task requires tools, entrypoints, or materials")
        if not self.scoring.dimensions:
            raise ValueError("executable task requires scoring dimensions")
        if self.coordination and not any(cap.name == "subagent_spawn" for cap in self.agent_capabilities):
            raise ValueError("coordination contract requires subagent_spawn capability")
        return self


def normalize_contract_bindings(contract: ExecutableTaskContract) -> ExecutableTaskContract:
    """Move valid typed bindings misplaced by a model into canonical fields.

    This is a deterministic structural normalization, not semantic repair: it
    only accepts payloads that already validate as MaterialContract /
    ScenarioContract and never invents missing bindings.
    """
    context = dict(contract.context)
    environment = contract.environment
    updates: dict[str, Any] = {}
    if not environment.materials and isinstance(context.get("material_contracts"), list):
        try:
            updates["materials"] = [MaterialContract.model_validate(value) for value in context["material_contracts"]]
            context.pop("material_contracts", None)
        except Exception:
            pass
    if environment.scenario is None and isinstance(context.get("scenario"), dict):
        try:
            updates["scenario"] = ScenarioContract.model_validate(context["scenario"])
            context.pop("scenario", None)
        except Exception:
            pass
    if not updates:
        return contract
    return contract.model_copy(update={
        "context": context,
        "environment": environment.model_copy(update=updates),
    })


class BenchmarkItem(Model):
    item_id: str
    dimension_id: str
    covered_dimension_ids: list[str] = Field(default_factory=list)
    source_mode: SourceMode
    source_id: str
    item_kind: Literal["static_question", "executable_task"] = "static_question"
    question: str | None = None
    context: str | None = None
    options: list[str] | None = None
    answer: str | None = None
    answer_type: str = "multiple_choice"
    executable_task: ExecutableTaskContract | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    status: ItemStatus = ItemStatus.CANDIDATE
    warnings: list[str] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    generation_log: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_item_form(self) -> "BenchmarkItem":
        if not self.covered_dimension_ids:
            self.covered_dimension_ids = [self.dimension_id]
        if self.item_kind == "executable_task":
            if self.executable_task is None:
                raise ValueError("executable_task item requires executable_task contract")
            if self.options is not None or self.answer_type == "multiple_choice":
                raise ValueError("executable_task must not be represented as multiple choice")
        else:
            if not self.question or not self.answer:
                raise ValueError("static_question requires question and answer")
            if self.answer_type == "multiple_choice" and (not self.options or len(self.options) < 2):
                raise ValueError("multiple_choice item requires at least two options")
        return self


class ReplenishmentRequest(Model):
    dimension_id: str
    source_id: str
    count: int = Field(default=1, ge=1)
    reason: str
    attempts: int = Field(default=0, ge=0)


class BenchmarkEvent(Model):
    event_id: str
    event_type: str
    role: str
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Benchmark(Model):
    benchmark_id: str
    schema_version: str = "benchmark.forge.v1"
    user_goal: UserGoal
    dimensions: list[BenchmarkDimension] = Field(default_factory=list)
    groundings: list[BenchmarkGrounding] = Field(default_factory=list)
    allocations: list[Allocation] = Field(default_factory=list)
    candidates: list[BenchmarkItem] = Field(default_factory=list)
    items: list[BenchmarkItem] = Field(default_factory=list)
    rejected_items: list[BenchmarkItem] = Field(default_factory=list)
    replenishment_requests: list[ReplenishmentRequest] = Field(default_factory=list)
    provider_offsets: dict[str, int] = Field(default_factory=dict)
    status: BenchmarkStatus = BenchmarkStatus.DRAFT
    warnings: list[str] = Field(default_factory=list)
    events: list[BenchmarkEvent] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)
