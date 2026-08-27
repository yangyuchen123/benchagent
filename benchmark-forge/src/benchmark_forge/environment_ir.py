from __future__ import annotations

"""Lowering and linking contracts for generated executable environments.

``ExecutableTaskContract`` is the benchmark-facing semantic specification. This
module introduces the implementation-facing intermediate representation (IR):
canonical tool/state/artifact/evidence identifiers shared by task, runtime,
scorer, and test components.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import ExecutableTaskContract

_ID = re.compile(r"^[a-z][a-z0-9_-]*$")

CORE_IR_VERSION = "1.0"
CORE_IR_FEATURES = frozenset({
    "tool_registry",
    "runtime_state",
    "artifact_registry",
    "evidence_authority",
    "rubric_binding",
    "task_binding",
    "workspace_policy",
})
IR_EXTENSION_VERSIONS = {
    "coordination_graph": "1.1",
    "material_registry": "1.2",
    "scenario_model": "1.2",
    "artifact_schema": "1.3",
}
SUPPORTED_IR_FEATURES = frozenset(CORE_IR_FEATURES | IR_EXTENSION_VERSIONS.keys())


class IRExpressivenessError(ValueError):
    """Contract semantics cannot be represented by the current IR language."""

    def __init__(self, message: str, *, missing_features: list[str],
                 affected_constructs: list[str], contract_gap: bool = True):
        super().__init__(message)
        self.missing_features = missing_features
        self.affected_constructs = affected_constructs
        # A Contract gap must stop codegen. An unknown feature invented by a
        # compiler draft is malformed output and is safely rewriteable.
        self.contract_gap = contract_gap


def _contract_semantic_gaps(contract: ExecutableTaskContract) -> tuple[list[str], list[str]]:
    """Detect explicit semantics that the current Core IR does not model.

    This is intentionally conservative: an expressiveness gap is raised only
    for constructs that are represented in the typed Contract but have no Core
    IR construct. It never downgrades the task to fit the current schema.
    """
    missing: list[str] = []
    affected: list[str] = []
    raw = {str(k).lower() for k in contract.constraints}
    for key, feature, construct in [
        ("fault_injection", "fault_model", "constraints.fault_injection"),
        ("faults", "fault_model", "constraints.faults"),
        ("resource_locks", "resource_model", "constraints.resource_locks"),
        ("resource_locking", "resource_model", "constraints.resource_locking"),
        ("cross_session", "session_registry", "constraints.cross_session"),
        ("cross_session_handoff", "session_registry", "constraints.cross_session_handoff"),
        ("human_approval", "human_interaction", "constraints.human_approval"),
        ("shared_mutable_state", "shared_state", "constraints.shared_mutable_state"),
        ("repair_lifecycle", "repair_lifecycle", "constraints.repair_lifecycle"),
    ]:
        if key in raw and feature not in missing:
            missing.append(feature)
            affected.append(construct)
    if missing:
        return missing, affected
    return [], []


class IRModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _id(value: str, *, fallback: str = "item") -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_").lower()
    if not value or not re.match(r"^[a-z]", value):
        value = f"{fallback}_{value}" if value else fallback
    return value


class IRTool(IRModel):
    tool_id: str
    name: str
    ownership: Literal["benchmark_environment", "agent_runtime", "evaluation_system"] = "benchmark_environment"
    interface: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    trace_event: str | None = None
    entrypoint: dict[str, Any] = Field(default_factory=dict)


class IRStateField(IRModel):
    state_id: str
    path: str
    value_type: str = "object"
    producer: str | None = None
    producer_field: str | None = None
    consumers: list[str] = Field(default_factory=list)
    authority: Literal["environment_state", "runtime_trace", "workspace_artifact", "agent_report"] = "environment_state"


class IRArtifact(IRModel):
    artifact_id: str
    path: str
    media_type: str | None = None
    description: str = ""
    required: bool = True
    schema_path: str | None = None
    schema_def: dict[str, Any] = Field(default_factory=dict)
    producer: Literal["agent_workspace", "runtime", "environment", "system"] = "agent_workspace"
    authority: Literal["workspace_artifact", "environment_state", "runtime_trace", "system"] = "workspace_artifact"
    must_exist_before: str | None = None


class IREvidence(IRModel):
    evidence_id: str
    source_type: Literal["artifact", "environment_state", "tool_trace", "agent_trajectory", "verifier"]
    authority: Literal["environment_runtime", "workspace_artifact", "system_verifier", "agent_report"]
    schema_ref: str = ""
    read_interface: str = ""
    allowed_consumers: list[str] = Field(default_factory=list)


class IRRubricCriterion(IRModel):
    criterion_id: str
    description: str
    weight: float = Field(default=0, ge=0)
    minimum_score: float | None = Field(default=None, ge=0, le=100)
    critical_gate: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    state_refs: list[str] = Field(default_factory=list)
    self_report_allowed: bool = False


class IRRubric(IRModel):
    rubric_id: str
    pass_threshold: float = Field(ge=0, le=100)
    deterministic: bool = True
    criteria: list[IRRubricCriterion]


class IRTaskBinding(IRModel):
    task_id: str
    instruction: str
    tool_refs: list[str] = Field(default_factory=list)
    material_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    state_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class IRWorkspace(IRModel):
    writable_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=lambda: ["private", "expected", "scorer"])
    network: Literal["forbidden", "restricted", "allowed"] = "forbidden"


class IRMaterial(IRModel):
    material_id: str
    source_type: Literal["path", "git", "registry", "generated", "knowledge"]
    source_ref: str
    target: str
    read_only: bool = True
    required: bool = True
    visibility: Literal["agent", "evaluation_system"] = "agent"
    schema_ref: str | None = None
    minimum_items: int | None = Field(default=None, ge=0)
    collection_key: str | None = None


class IRScenario(IRModel):
    data_dependent: bool = False
    material_refs: list[str] = Field(default_factory=list)
    runtime_generator_ref: str | None = None
    evaluation_injection_ref: str | None = None
    allow_empty: bool = False
    minimum_items: int = Field(default=1, ge=0)
    required_case_tags: list[str] = Field(default_factory=list)
    case_tag_field: str | None = None


class IRCoordinationNode(IRModel):
    node_id: str
    objective: str
    depends_on: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    acceptance_checks: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(default_factory=list)


class IRCoordinationGraph(IRModel):
    graph_id: str
    nodes: list[IRCoordinationNode]
    min_subagents: int = Field(default=1, ge=1)
    max_subagents: int | None = Field(default=None, ge=1)
    require_distinct_assignment: bool = True
    require_parallel_independent_work: bool = False
    require_agent_attribution: bool = True
    require_acceptance_evidence: bool = True
    repair_budget: int = Field(default=1, ge=0)


class IRComponentSpec(IRModel):
    component_id: Literal["manifest", "runtime", "scorer", "tests"]
    owned_paths: list[str]
    depends_on: list[str] = Field(default_factory=list)


class EnvironmentIR(IRModel):
    schema_version: str = "benchmark-forge.environment-ir.v1"
    environment_id: str
    task_id: str
    protocol: str
    tools: list[IRTool] = Field(default_factory=list)
    materials: list[IRMaterial] = Field(default_factory=list)
    scenario: IRScenario | None = None
    runtime_state: list[IRStateField] = Field(default_factory=list)
    artifacts: list[IRArtifact] = Field(default_factory=list)
    evidence: list[IREvidence] = Field(default_factory=list)
    rubric: IRRubric
    task_binding: IRTaskBinding
    workspace: IRWorkspace
    coordination: IRCoordinationGraph | None = None
    components: list[IRComponentSpec]
    provenance: dict[str, Any] = Field(default_factory=dict)
    frozen: bool = False
    ir_version: str = CORE_IR_VERSION
    required_features: list[str] = Field(default_factory=lambda: sorted(CORE_IR_FEATURES))
    ir_checksum: str | None = None
    frozen_at: datetime | None = None

    @model_validator(mode="after")
    def validate_ir(self) -> "EnvironmentIR":
        for value in [self.environment_id, self.task_id, *(tool.tool_id for tool in self.tools), *(a.artifact_id for a in self.artifacts)]:
            if not _ID.match(value):
                raise ValueError(f"invalid IR identifier: {value}")
        tool_ids = {tool.tool_id for tool in self.tools}
        material_ids = {material.material_id for material in self.materials}
        artifact_ids = {artifact.artifact_id for artifact in self.artifacts}
        state_ids = {state.state_id for state in self.runtime_state}
        evidence_ids = {evidence.evidence_id for evidence in self.evidence}
        if len(tool_ids) != len(self.tools):
            raise ValueError("duplicate tool_id")
        if len(material_ids) != len(self.materials):
            raise ValueError("duplicate material_id")
        for material in self.materials:
            if not _ID.match(material.material_id):
                raise ValueError(f"invalid IR identifier: {material.material_id}")
            target_path = Path(material.target)
            if target_path.is_absolute() or ".." in target_path.parts:
                raise ValueError(f"unsafe material target: {material.target}")
        if self.scenario:
            unknown_materials = set(self.scenario.material_refs) - material_ids
            if unknown_materials:
                raise ValueError(f"scenario references unknown materials: {sorted(unknown_materials)}")
        if len(artifact_ids) != len(self.artifacts):
            raise ValueError("duplicate artifact_id")
        if len(state_ids) != len(self.runtime_state):
            raise ValueError("duplicate state_id")
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("duplicate evidence_id")
        for criterion in self.rubric.criteria:
            unknown_evidence = set(criterion.evidence_refs) - evidence_ids
            unknown_artifacts = set(criterion.artifact_refs) - artifact_ids
            unknown_states = set(criterion.state_refs) - state_ids
            if unknown_evidence or unknown_artifacts or unknown_states:
                raise ValueError(
                    f"rubric {criterion.criterion_id} has unknown refs: "
                    f"evidence={sorted(unknown_evidence)}, artifacts={sorted(unknown_artifacts)}, states={sorted(unknown_states)}"
                )
        evaluation_tool_ids = {tool.tool_id for tool in self.tools if tool.ownership == "evaluation_system"}
        exposed_evaluation_tools = set(self.task_binding.tool_refs) & evaluation_tool_ids
        if exposed_evaluation_tools:
            raise ValueError(
                f"task_binding exposes evaluation_system tools to the Agent: {sorted(exposed_evaluation_tools)}"
            )
        if set(self.task_binding.tool_refs) - tool_ids:
            raise ValueError("task references unknown tools")
        if set(self.task_binding.material_refs) - material_ids:
            raise ValueError("task references unknown materials")
        if set(self.task_binding.artifact_refs) - artifact_ids:
            raise ValueError("task references unknown artifacts")
        if set(self.task_binding.state_refs) - state_ids:
            raise ValueError("task references unknown states")
        if set(self.task_binding.evidence_refs) - evidence_ids:
            raise ValueError("task references unknown evidence")
        if len({component.component_id for component in self.components}) != len(self.components):
            raise ValueError("duplicate component_id")
        component_ids = {component.component_id for component in self.components}
        for component in self.components:
            unknown_deps = set(component.depends_on) - component_ids
            if unknown_deps:
                raise ValueError(f"component {component.component_id} has unknown dependencies: {sorted(unknown_deps)}")
            if not component.owned_paths:
                raise ValueError(f"component {component.component_id} must own at least one path")
        artifact_paths = [artifact.path for artifact in self.artifacts]
        if len(set(artifact_paths)) != len(artifact_paths):
            raise ValueError("duplicate artifact path")
        schema_paths: list[str] = []
        for artifact in self.artifacts:
            artifact_path = Path(artifact.path)
            if artifact_path.is_absolute() or ".." in artifact_path.parts:
                raise ValueError(f"unsafe artifact path: {artifact.path}")
            if artifact.schema_path is not None:
                schema_path = Path(artifact.schema_path)
                if schema_path.is_absolute() or ".." in schema_path.parts:
                    raise ValueError(f"unsafe artifact schema path: {artifact.schema_path}")
                schema_paths.append(artifact.schema_path)
        if len(schema_paths) != len(set(schema_paths)):
            raise ValueError("duplicate artifact schema_path")
        typed_artifacts = [artifact for artifact in self.artifacts if _is_constraining_schema(artifact.schema_def)]
        if typed_artifacts and "artifact_schema" not in self.required_features:
            raise ValueError("typed artifact schemas must be declared through artifact_schema")
        if "artifact_schema" in self.required_features:
            if not typed_artifacts:
                raise ValueError("required feature artifact_schema has no constraining artifact schema")
            for artifact in typed_artifacts:
                if not artifact.schema_path:
                    raise ValueError(f"artifact {artifact.artifact_id} has schema_def but no schema_path")
        coordination_node_ids = {node.node_id for node in self.coordination.nodes} if self.coordination else set()
        rubric_criterion_ids = {criterion.criterion_id for criterion in self.rubric.criteria}
        for state in self.runtime_state:
            if state.producer and state.producer not in tool_ids and state.producer not in coordination_node_ids and state.producer not in rubric_criterion_ids and state.producer not in {"runtime", "environment", "system", "agent"}:
                raise ValueError(f"state {state.state_id} references unknown producer: {state.producer}")
            unknown_consumers = set(state.consumers) - tool_ids - coordination_node_ids - rubric_criterion_ids - {"runtime", "scorer", "tests", "agent"}
            if unknown_consumers:
                raise ValueError(f"state {state.state_id} references unknown consumers: {sorted(unknown_consumers)}")
        if self.coordination is not None:
            node_ids = {node.node_id for node in self.coordination.nodes}
            if len(node_ids) != len(self.coordination.nodes):
                raise ValueError("duplicate coordination node_id")
            for node in self.coordination.nodes:
                unknown_deps = set(node.depends_on) - node_ids
                if unknown_deps:
                    raise ValueError(f"coordination node {node.node_id} has unknown dependencies: {sorted(unknown_deps)}")
                forbidden_scope = set(node.write_scope) & set(self.workspace.forbidden_paths)
                if forbidden_scope:
                    raise ValueError(f"coordination node {node.node_id} writes forbidden paths: {sorted(forbidden_scope)}")
        if self.coordination is not None and "coordination_graph" not in self.required_features:
            raise ValueError("coordination graph must be declared in required_features")
        if "coordination_graph" in self.required_features and self.coordination is None:
            raise ValueError("required feature coordination_graph has no typed construct")
        if self.materials and "material_registry" not in self.required_features:
            raise ValueError("materials must be declared through material_registry")
        if "material_registry" in self.required_features and not self.materials:
            raise ValueError("required feature material_registry has no typed materials")
        if self.scenario is not None and "scenario_model" not in self.required_features:
            raise ValueError("scenario must be declared through scenario_model")
        if "scenario_model" in self.required_features and self.scenario is None:
            raise ValueError("required feature scenario_model has no typed construct")
        unknown_features = set(self.required_features) - SUPPORTED_IR_FEATURES
        if unknown_features:
            raise IRExpressivenessError(
                f"IR {self.ir_version} requires unsupported features: {sorted(unknown_features)}",
                missing_features=sorted(unknown_features), affected_constructs=[],
                contract_gap=False,
            )
        return self

    def freeze(self) -> "EnvironmentIR":
        """Freeze semantic bindings and attach a reproducible checksum."""
        if self.frozen:
            return self
        payload = self.model_dump(mode="json", exclude={"frozen", "ir_checksum", "frozen_at"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        checksum = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self.model_copy(update={
            "frozen": True,
            "ir_checksum": checksum,
            "frozen_at": datetime.now(timezone.utc),
        })

    def semantic_checksum(self) -> str:
        payload = self.model_dump(mode="json", exclude={"frozen", "ir_checksum", "frozen_at"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EnvironmentIRDraft(IRModel):
    """Unfrozen compiler-agent output. Cross-reference checks happen later."""
    schema_version: str = "benchmark-forge.environment-ir-draft.v1"
    environment_id: str
    task_id: str
    protocol: str = "octagon.env.v1"
    tools: list[IRTool] = Field(default_factory=list)
    materials: list[IRMaterial] = Field(default_factory=list)
    scenario: IRScenario | None = None
    runtime_state: list[IRStateField] = Field(default_factory=list)
    artifacts: list[IRArtifact] = Field(default_factory=list)
    evidence: list[IREvidence] = Field(default_factory=list)
    rubric: IRRubric
    task_binding: IRTaskBinding
    workspace: IRWorkspace = Field(default_factory=IRWorkspace)
    coordination: IRCoordinationGraph | None = None
    components: list[IRComponentSpec] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    ir_version: str = CORE_IR_VERSION
    required_features: list[str] = Field(default_factory=lambda: sorted(CORE_IR_FEATURES))


def default_component_specs() -> list[IRComponentSpec]:
    return [
        IRComponentSpec(component_id="manifest", owned_paths=["meta.yaml", "README.md", "tasks/", "materials/", "schemas/"], depends_on=[]),
        IRComponentSpec(component_id="runtime", owned_paths=["core.py", "mcp_server.py"], depends_on=["manifest"]),
        IRComponentSpec(component_id="scorer", owned_paths=["scorer.py", "scorer_fixtures/"], depends_on=["manifest", "runtime"]),
        IRComponentSpec(component_id="tests", owned_paths=["tests/"], depends_on=["manifest", "runtime", "scorer"]),
    ]


def normalize_ir_draft(draft: EnvironmentIRDraft) -> EnvironmentIR:
    """Normalize a compiler draft and perform all cross-reference checks."""
    payload = draft.model_dump(mode="python")
    default_components = {
        item.component_id: item.model_dump(mode="python")
        for item in default_component_specs()
    }
    if not payload.get("components"):
        payload["components"] = list(default_components.values())
    else:
        # Empty ownership is a structural hole, not a new language feature.
        # Complete it from the stable protocol before semantic validation;
        # unknown paths/components are still rejected by the linker.
        normalized_components = []
        for component in payload["components"]:
            component = dict(component)
            default = default_components.get(component.get("component_id"))
            if default:
                # Component ownership is Forge protocol, not an Agent design
                # choice. Semantic paths such as runtime.state.* must never
                # replace filesystem ownership roots.
                component["owned_paths"] = default["owned_paths"]
                component["depends_on"] = default["depends_on"]
            normalized_components.append(component)
        payload["components"] = normalized_components
    payload["frozen"] = False
    payload["ir_version"] = str(payload.get("ir_version", CORE_IR_VERSION) or CORE_IR_VERSION)
    payload["required_features"] = list(payload.get("required_features") or sorted(CORE_IR_FEATURES))
    for field_name, feature in (
        ("coordination", "coordination_graph"),
        ("materials", "material_registry"),
        ("scenario", "scenario_model"),
    ):
        if payload.get(field_name) and feature not in payload["required_features"]:
            payload["required_features"].append(feature)
    feature_versions = [IR_EXTENSION_VERSIONS[feature] for feature in payload["required_features"] if feature in IR_EXTENSION_VERSIONS]
    if feature_versions:
        payload["ir_version"] = max([payload.get("ir_version", CORE_IR_VERSION), *feature_versions], key=lambda value: tuple(map(int, value.split("."))))
    unknown = sorted(set(payload["required_features"]) - SUPPORTED_IR_FEATURES)
    if unknown:
        raise IRExpressivenessError(
            f"IR {payload.get('ir_version', CORE_IR_VERSION)} requires unsupported features: {unknown}",
            missing_features=unknown, affected_constructs=[], contract_gap=False,
        )
    return EnvironmentIR.model_validate(payload).freeze()


def analyze_contract_expressiveness(contract: ExecutableTaskContract) -> None:
    missing, affected = _contract_semantic_gaps(contract)
    if missing:
        raise IRExpressivenessError(
            "Contract requires IR extensions before compilation: "
            f"missing_features={missing}; affected_constructs={affected}",
            missing_features=missing, affected_constructs=affected,
        )


def _is_constraining_schema(schema: dict[str, Any] | None) -> bool:
    """Return true when a JSON schema defines named public output structure."""
    if not isinstance(schema, dict) or not schema:
        return False
    for union_key in ("oneOf", "anyOf"):
        branches = schema.get(union_key)
        if isinstance(branches, list) and branches:
            return all(_is_constraining_schema(branch) for branch in branches)
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        return (
            isinstance(properties, dict) and bool(properties)
            and isinstance(required, list) and bool(required)
            and set(required) <= set(properties)
        )
    if schema_type == "array":
        return isinstance(schema.get("items"), dict) and _is_constraining_schema(schema["items"])
    return schema_type in {"string", "number", "integer", "boolean"} or any(
        key in schema for key in ("const", "enum", "$ref")
    )


def _schema_object_branches(schema: dict[str, Any]) -> list[dict[str, Any]]:
    for union_key in ("oneOf", "anyOf"):
        branches = schema.get(union_key)
        if isinstance(branches, list):
            result: list[dict[str, Any]] = []
            for branch in branches:
                if isinstance(branch, dict):
                    result.extend(_schema_object_branches(branch))
            return result
    return [schema] if schema.get("type") == "object" else []


def _schema_has_case_collection(schema: dict[str, Any], minimum_items: int) -> bool:
    """Detect a required per-case array capable of representing scenario coverage."""
    for branch in _schema_object_branches(schema):
        properties = branch.get("properties")
        required = set(branch.get("required", []))
        if not isinstance(properties, dict):
            continue
        for name, child in properties.items():
            if name not in required or not isinstance(child, dict) or child.get("type") != "array":
                continue
            if child.get("minItems", 0) < minimum_items:
                continue
            item = child.get("items")
            if not isinstance(item, dict) or item.get("type") != "object":
                continue
            item_properties = item.get("properties")
            item_required = item.get("required")
            if isinstance(item_properties, dict) and isinstance(item_required, list) and len(item_required) >= 2:
                return True
    return False


def _schema_forbids_unrequested_top_level_fields(schema: dict[str, Any]) -> bool:
    branches = _schema_object_branches(schema)
    return bool(branches) and all(branch.get("additionalProperties") is False for branch in branches)


def _artifact_contract_requires_schema(contract: ExecutableTaskContract, artifact: Any) -> bool:
    if (artifact.media_type or "").lower() not in {"application/json", "json"}:
        return False
    language = f"{artifact.description} {contract.instruction}".lower()
    if any(token in language for token in ("schema", "structured", "结构化", "模式")):
        return True
    for tool in [*contract.environment.tools, *contract.environment.entrypoints.values()]:
        name = tool.name.lower() if hasattr(tool, "name") else ""
        description = tool.description.lower() if hasattr(tool, "description") else ""
        if "validat" in name or "schema" in description:
            return True
    return False


def lower_contract_to_ir(contract: ExecutableTaskContract) -> EnvironmentIR:
    """Lower a semantic task contract into canonical implementation bindings."""
    analyze_contract_expressiveness(contract)
    tools: list[IRTool] = []
    for tool in contract.environment.tools:
        tool_id = _id(tool.name, fallback="tool")
        entrypoint = dict(tool.entrypoint)
        tools.append(IRTool(
            tool_id=tool_id, name=tool.name, ownership=tool.ownership,
            interface=tool.interface, description=tool.description, entrypoint=entrypoint,
            input_schema=entrypoint.get("input_schema", {"type": "object"}),
            output_schema=entrypoint.get("output_schema", {"type": "object"}),
            trace_event=entrypoint.get("trace_event", f"tool_call.{tool_id}"),
        ))

    materials = [IRMaterial(
        material_id=material.material_id,
        source_type=material.source.type,
        source_ref=material.source.ref,
        target=material.target,
        read_only=material.read_only,
        required=material.required,
        visibility=material.visibility,
        schema_ref=material.schema_ref,
        minimum_items=material.minimum_items,
        collection_key=material.collection_key,
    ) for material in contract.environment.materials]
    scenario = None
    if contract.environment.scenario is not None:
        source = contract.environment.scenario
        scenario = IRScenario(
            data_dependent=source.data_dependent,
            material_refs=source.material_refs,
            runtime_generator_ref=source.runtime_generator_ref.ref if source.runtime_generator_ref else None,
            evaluation_injection_ref=source.evaluation_injection_ref.ref if source.evaluation_injection_ref else None,
            allow_empty=source.allow_empty,
            minimum_items=source.minimum_items,
            required_case_tags=source.required_case_tags,
            case_tag_field=source.case_tag_field,
        )

    artifacts: list[IRArtifact] = []
    for requirement in contract.artifacts:
        artifact_id = _id(requirement.path.rsplit("/", 1)[-1].rsplit(".", 1)[0], fallback="artifact")
        if any(existing.artifact_id == artifact_id for existing in artifacts):
            artifact_id = f"{artifact_id}_{len(artifacts) + 1}"
        artifacts.append(IRArtifact(
            artifact_id=artifact_id, path=requirement.path,
            media_type=requirement.media_type, description=requirement.description,
            required=requirement.required, schema_path=requirement.schema_path,
            schema_def=requirement.schema_def or {},
        ))

    evidence: list[IREvidence] = []
    for source in sorted({source for dimension in contract.scoring.dimensions for source in dimension.evidence_sources}):
        authority = {
            "artifact": "workspace_artifact",
            "environment_state": "environment_runtime",
            "tool_trace": "environment_runtime",
            "agent_trajectory": "agent_report",
            "verifier": "system_verifier",
        }[source]
        evidence.append(IREvidence(
            evidence_id=f"evidence_{source}", source_type=source, authority=authority,
            schema_ref=f"{source}.v1", read_interface=f"runtime.{source}",
        ))

    artifact_ids = [artifact.artifact_id for artifact in artifacts]
    evidence_ids = [item.evidence_id for item in evidence]
    tool_ids = [item.tool_id for item in tools]
    criteria = []
    for dimension in contract.scoring.dimensions:
        refs = [f"evidence_{source}" for source in dimension.evidence_sources]
        criteria.append(IRRubricCriterion(
            criterion_id=_id(dimension.name, fallback="criterion"),
            description=dimension.description, weight=dimension.weight,
            minimum_score=dimension.minimum_score, critical_gate=dimension.critical_gate,
            evidence_refs=[ref for ref in refs if ref in evidence_ids],
            artifact_refs=artifact_ids if "artifact" in dimension.evidence_sources else [],
            self_report_allowed=False,
        ))
    state_refs = [state.state_id for state in []]
    coordination = None
    required_features = set(CORE_IR_FEATURES)
    if materials:
        required_features.add("material_registry")
    if scenario is not None:
        required_features.add("scenario_model")
    if any(_is_constraining_schema(artifact.schema_def) for artifact in artifacts):
        required_features.add("artifact_schema")
    if contract.coordination is not None:
        coordination = IRCoordinationGraph(
            graph_id=_id(contract.task_id + "_coordination", fallback="coordination"),
            nodes=[IRCoordinationNode(
                node_id=_id(node.subtask_id, fallback="subtask"), objective=node.objective,
                depends_on=[_id(dep, fallback="subtask") for dep in node.depends_on],
                required_context=node.required_context, output_contract=node.output_contract,
                acceptance_checks=node.acceptance_checks, write_scope=node.write_scope,
            ) for node in contract.coordination.subtasks],
            min_subagents=contract.coordination.min_subagents,
            max_subagents=contract.coordination.max_subagents,
            require_distinct_assignment=contract.coordination.require_distinct_assignment,
            require_parallel_independent_work=contract.coordination.require_parallel_independent_work,
            require_agent_attribution=contract.coordination.require_agent_attribution,
            require_acceptance_evidence=contract.coordination.require_acceptance_evidence,
            repair_budget=contract.coordination.repair_budget,
        )
        required_features.add("coordination_graph")
    return EnvironmentIR(
        environment_id=contract.environment.environment_id,
        task_id=contract.task_id, protocol=contract.environment.protocol,
        tools=tools, materials=materials, scenario=scenario,
        runtime_state=[], artifacts=artifacts, evidence=evidence,
        rubric=IRRubric(
            rubric_id=_id(contract.task_id, fallback="rubric"),
            pass_threshold=contract.scoring.pass_threshold or 0,
            deterministic=contract.scoring.deterministic, criteria=criteria,
        ),
        task_binding=IRTaskBinding(
            task_id=contract.task_id, instruction=contract.instruction,
            tool_refs=[tool.tool_id for tool in tools if tool.ownership != "evaluation_system"],
            material_refs=[material.material_id for material in materials if material.visibility == "agent"],
            artifact_refs=artifact_ids,
            state_refs=state_refs, evidence_refs=evidence_ids,
        ),
        workspace=IRWorkspace(
            writable_paths=contract.environment.workspace.writable_paths,
            forbidden_paths=contract.environment.workspace.forbidden_paths,
            network=contract.environment.workspace.network,
        ),
        coordination=coordination,
        components=[
            IRComponentSpec(component_id="manifest", owned_paths=["meta.yaml", "README.md", "tasks/", "materials/", "schemas/"], depends_on=[]),
            IRComponentSpec(component_id="runtime", owned_paths=["core.py", "mcp_server.py"], depends_on=["manifest"]),
            IRComponentSpec(component_id="scorer", owned_paths=["scorer.py", "scorer_fixtures/"], depends_on=["manifest", "runtime"]),
            IRComponentSpec(component_id="tests", owned_paths=["tests/"], depends_on=["manifest", "runtime", "scorer"]),
        ],
        provenance={"source": "contract-lowering", "contract_task_id": contract.task_id},
        ir_version=max(
            [CORE_IR_VERSION, *(IR_EXTENSION_VERSIONS[f] for f in required_features if f in IR_EXTENSION_VERSIONS)],
            key=lambda value: tuple(map(int, value.split("."))),
        ),
        required_features=sorted(required_features),
    )


def validate_ir_contract_bindings(contract: ExecutableTaskContract, ir: EnvironmentIR) -> None:
    """Ensure Compiler Agent preserved typed semantics and completed bounded holes."""
    expected_artifacts = {artifact.path: artifact for artifact in contract.artifacts}
    actual_artifacts = {artifact.path: artifact for artifact in ir.artifacts}
    if set(expected_artifacts) != set(actual_artifacts):
        raise ValueError(
            f"IR artifact bindings mismatch: expected={sorted(expected_artifacts)}, actual={sorted(actual_artifacts)}"
        )
    for path, expected in expected_artifacts.items():
        actual = actual_artifacts[path]
        if expected.schema_def and actual.schema_def != expected.schema_def:
            raise ValueError(f"IR artifact schema_def changed explicit Contract schema for {path}")
        if expected.schema_path and actual.schema_path != expected.schema_path:
            raise ValueError(f"IR artifact schema_path changed explicit Contract binding for {path}")
        if _artifact_contract_requires_schema(contract, expected):
            if not _is_constraining_schema(actual.schema_def):
                raise ValueError(
                    f"artifact {path} is described as schema-valid/structured but IR schema_def is unconstrained; "
                    "complete the artifact-schema hole with named required fields, not an arbitrary non-empty object"
                )
            if "unrequested field" in contract.instruction.lower() and not _schema_forbids_unrequested_top_level_fields(actual.schema_def):
                raise ValueError(
                    f"artifact {path} schema permits unrequested top-level fields despite the Contract; "
                    "set additionalProperties=false on every public object branch"
                )
            coverage_language = " ".join(
                dimension.description.lower() for dimension in contract.scoring.dimensions
            )
            scenario = contract.environment.scenario
            if (
                scenario is not None and scenario.data_dependent and scenario.minimum_items > 1
                and any(token in coverage_language for token in ("all required", "all fixture", "all cases", "coverage", "每个", "全部"))
                and not _schema_has_case_collection(actual.schema_def, scenario.minimum_items)
            ):
                raise ValueError(
                    f"artifact {path} schema cannot represent required per-case coverage; define a required array "
                    f"with minItems>={scenario.minimum_items} whose object items require both an identity field and a response field"
                )
            if not actual.schema_path:
                raise ValueError(f"artifact {path} requires a canonical public schema_path")
            if "artifact_schema" not in ir.required_features:
                raise ValueError(f"artifact {path} requires required_feature artifact_schema")
    expected_materials = {material.material_id for material in contract.environment.materials}
    actual_materials = {material.material_id for material in ir.materials}
    if expected_materials != actual_materials:
        raise ValueError(
            f"IR material bindings mismatch: expected={sorted(expected_materials)}, actual={sorted(actual_materials)}"
        )
    expected_public = {
        material.material_id for material in contract.environment.materials
        if material.visibility == "agent"
    }
    if set(ir.task_binding.material_refs) != expected_public:
        raise ValueError("IR task material_refs do not match public Contract materials")
    contract_scenario = contract.environment.scenario
    if (contract_scenario is None) != (ir.scenario is None):
        raise ValueError("IR scenario binding presence does not match Contract")
    if contract_scenario and ir.scenario:
        if set(contract_scenario.material_refs) != set(ir.scenario.material_refs):
            raise ValueError("IR scenario material_refs do not match Contract")
        if contract_scenario.minimum_items != ir.scenario.minimum_items or contract_scenario.allow_empty != ir.scenario.allow_empty:
            raise ValueError("IR scenario minimum/empty policy does not match Contract")


class IRValidationError(ValueError):
    pass


class IRComponentFile(IRModel):
    path: str
    content: str
    purpose: str = ""


class IRComponentOutput(IRModel):
    component_id: Literal["manifest", "runtime", "scorer", "tests"]
    files: list[IRComponentFile]
    implementation_notes: list[str] = Field(default_factory=list)


def component_output_from_bundle(ir: EnvironmentIR, bundle: Any, component_id: str) -> IRComponentOutput:
    """Project a linked bundle back to one owned component for repair."""
    component = next((item for item in ir.components if item.component_id == component_id), None)
    if component is None:
        raise IRValidationError(f"unknown component: {component_id}")
    files = [
        IRComponentFile(path=file.path, content=file.content, purpose=file.purpose)
        for file in bundle.files
        if any(_path_owned(file.path, root) for root in component.owned_paths)
    ]
    return IRComponentOutput(component_id=component_id, files=files, implementation_notes=["projected from linked bundle"])


def _path_owned(path: str, root: str) -> bool:
    """Match ownership roots without treating ``core.pyx`` as ``core.py``."""
    normalized = path.strip("/")
    root = root.strip("/")
    return normalized == root or normalized.startswith(root.rstrip("/") + "/")


def link_component_outputs(ir: EnvironmentIR, outputs: list[IRComponentOutput]):
    """Link component outputs into an EnvironmentScaffoldBundle.

    The import is intentionally lazy to keep the IR module independent from the
    staging registry and avoid a module cycle.
    """
    if not ir.frozen:
        raise IRValidationError("IR must be frozen before component linking")
    expected = {component.component_id for component in ir.components}
    output_ids = [output.component_id for output in outputs]
    if len(set(output_ids)) != len(output_ids):
        raise IRValidationError("duplicate component output")
    actual = set(output_ids)
    unknown_components = actual - expected
    if unknown_components:
        raise IRValidationError(f"unknown component outputs: {sorted(unknown_components)}")
    missing = expected - actual
    if missing:
        raise IRValidationError(f"missing component outputs: {sorted(missing)}")
    owners: dict[str, str] = {}
    files = []
    notes: list[str] = []
    for output in outputs:
        component = next(item for item in ir.components if item.component_id == output.component_id)
        for file in output.files:
            if file.path in owners:
                raise IRValidationError(f"component path collision: {file.path} ({owners[file.path]} / {output.component_id})")
            if not any(_path_owned(file.path, root) for root in component.owned_paths):
                raise IRValidationError(f"component {output.component_id} does not own path: {file.path}")
            owners[file.path] = output.component_id
            files.append(file)
        notes.extend(output.implementation_notes)
    from .staging import EnvironmentScaffoldBundle, ScaffoldFile
    bundle = EnvironmentScaffoldBundle(
        environment_id=ir.environment_id,
        files=[ScaffoldFile(path=file.path, content=file.content, purpose=file.purpose) for file in files],
        implementation_notes=[f"linked from environment-ir.v{ir.ir_version}", f"compiled_from_ir_checksum={ir.ir_checksum or ir.semantic_checksum()}", *notes],
    )
    bundle.validate_paths()
    return bundle
