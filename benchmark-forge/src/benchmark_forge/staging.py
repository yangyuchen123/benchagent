from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import Benchmark, BenchmarkItem, normalize_environment_id
from .scorer_design import ScorerDesign, ScorerReview
from .rubric_review import RubricIntegrityReview
from .environment_ir import EnvironmentIR, lower_contract_to_ir
from .agent_octagon_abi import material_mounts, mcp_entrypoint, validate_bundle_abi


class CandidateStatus(StrEnum):
    GENERATED = "generated_contract"
    STATIC_VALIDATED = "static_validated"
    SCAFFOLDED = "scaffolded"
    SMOKE_TESTED = "smoke_tested"
    PILOT_SCORED = "pilot_scored"
    NEEDS_REPAIR = "needs_repair"
    REQUIRES_IR_EXTENSION = "requires_ir_extension"
    SCENARIO_INCOMPLETE = "scenario_incomplete"
    APPROVED = "approved"
    PROMOTION_READY = "promotion_ready"
    REJECTED = "rejected"


class CandidateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: str
    stage: Literal["static", "scaffold", "smoke", "runtime", "scoring", "human"]
    status: Literal["pending", "passed", "warning", "failed"]
    summary: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PilotTrial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trial_ref: str
    agent_id: str
    score: float = Field(ge=0, le=100)
    passed: bool
    attempt_status: str | None = None
    execution_status: str | None = None
    scoring_status: str | None = None
    trial_result_checksum: str | None = None


class AgentEvalReportRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_ref: str
    report_checksum: str | None = None
    benchmark_quality_score: float = Field(ge=0, le=100)
    human_alignment_score: float = Field(ge=0, le=100)
    difficulty_score: float = Field(ge=0, le=100)
    notes: str = ""


class HumanApproval(BaseModel):
    approver: str
    approved: bool
    notes: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PromotionPolicy(BaseModel):
    required_checks: list[str] = Field(default_factory=lambda: [
        "agent_subject_construct", "scenario_contract", "scenario_completeness", "contract_schema", "agent_octagon_abi", "provenance_safety", "scaffold_integrity",
        "scorer_semantic_review", "environment_smoke", "scorer_smoke", "artifact_collection",
    ])
    min_successful_trials: int = Field(default=2, ge=1)
    min_distinct_agents: int = Field(default=2, ge=1)
    min_agent_eval_quality: float = Field(default=60, ge=0, le=100)
    min_human_alignment: float = Field(default=60, ge=0, le=100)
    require_human_approval: bool = True

    @model_validator(mode="after")
    def require_semantic_scorer_review(self) -> "PromotionPolicy":
        # Migrate persisted v1 candidates: a syntactically runnable scorer is not
        # sufficient evidence that it measures the advertised capability.
        if "scorer_semantic_review" not in self.required_checks:
            insert_at = self.required_checks.index("scorer_smoke") if "scorer_smoke" in self.required_checks else len(self.required_checks)
            self.required_checks.insert(insert_at, "scorer_semantic_review")
        if "agent_octagon_abi" not in self.required_checks:
            insert_at = self.required_checks.index("provenance_safety") if "provenance_safety" in self.required_checks else 0
            self.required_checks.insert(insert_at, "agent_octagon_abi")
        return self


class PromotionReadiness(BaseModel):
    ready: bool
    blockers: list[str] = Field(default_factory=list)


class EnvironmentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "benchmark-forge.environment-candidate.v1"
    candidate_id: str
    benchmark_id: str
    item_id: str
    environment_id: str
    status: CandidateStatus = CandidateStatus.GENERATED
    item: BenchmarkItem
    environment_ir: EnvironmentIR | None = None
    scorer_design: ScorerDesign | None = None
    scorer_reviews: list[ScorerReview] = Field(default_factory=list)
    checks: list[CandidateCheck] = Field(default_factory=list)
    pilot_trials: list[PilotTrial] = Field(default_factory=list)
    agent_eval_reports: list[AgentEvalReportRef] = Field(default_factory=list)
    human_approval: HumanApproval | None = None
    promotion_policy: PromotionPolicy = Field(default_factory=PromotionPolicy)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def readiness(self) -> PromotionReadiness:
        blockers: list[str] = []
        checks = {check.check_id: check for check in self.checks}
        for check_id in self.promotion_policy.required_checks:
            check = checks.get(check_id)
            if check is None:
                blockers.append(f"missing required check: {check_id}")
            elif check.status != "passed":
                blockers.append(f"required check not passed: {check_id}={check.status}")
        successful = [trial for trial in self.pilot_trials if trial.passed]
        if len(successful) < self.promotion_policy.min_successful_trials:
            blockers.append(f"successful pilot trials {len(successful)}/{self.promotion_policy.min_successful_trials}")
        agents = {trial.agent_id for trial in successful}
        if len(agents) < self.promotion_policy.min_distinct_agents:
            blockers.append(f"distinct successful agents {len(agents)}/{self.promotion_policy.min_distinct_agents}")
        if not self.agent_eval_reports:
            blockers.append("missing agent-eval quality report")
        else:
            best = max(self.agent_eval_reports, key=lambda report: report.benchmark_quality_score)
            if best.benchmark_quality_score < self.promotion_policy.min_agent_eval_quality:
                blockers.append("agent-eval benchmark quality below threshold")
            if best.human_alignment_score < self.promotion_policy.min_human_alignment:
                blockers.append("agent-eval human alignment below threshold")
        if self.promotion_policy.require_human_approval and not (self.human_approval and self.human_approval.approved):
            blockers.append("human approval required")
        return PromotionReadiness(ready=not blockers, blockers=blockers)


class EnvironmentCandidateRegistry:
    """Isolated staging registry; never writes to agent-octagon-envs."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, candidate_id: str) -> Path:
        return self.root / candidate_id / "candidate.json"

    def save(self, candidate: EnvironmentCandidate) -> Path:
        candidate.updated_at = datetime.now(timezone.utc)
        path = self._path(candidate.candidate_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, candidate_id: str) -> EnvironmentCandidate:
        return EnvironmentCandidate.model_validate_json(self._path(candidate_id).read_text(encoding="utf-8"))

    def stage(self, benchmark: Benchmark, item: BenchmarkItem) -> EnvironmentCandidate:
        if item.item_kind != "executable_task" or item.executable_task is None:
            raise ValueError("only executable tasks can become environment candidates")
        scenario_errors = validate_contract_realizability(item)
        environment_ir = None if scenario_errors else lower_contract_to_ir(item.executable_task).freeze()
        candidate = EnvironmentCandidate(
            candidate_id=f"candidate-{uuid4()}", benchmark_id=benchmark.benchmark_id,
            item_id=item.item_id, environment_id=item.executable_task.environment.environment_id,
            item=item, environment_ir=environment_ir,
            checks=[CandidateCheck(
                check_id="scenario_contract", stage="static",
                status="failed" if scenario_errors else "passed",
                summary="; ".join(scenario_errors) if scenario_errors else "scenario dependencies have typed material/generator bindings",
                evidence_refs=[],
            )],
            status=CandidateStatus.SCENARIO_INCOMPLETE if scenario_errors else CandidateStatus.GENERATED,
        )
        self.save(candidate)
        return candidate

    def clear_environment_ir(self, candidate_id: str) -> EnvironmentCandidate:
        """Remove the staging projection when Agent IR compilation failed."""
        candidate = self.load(candidate_id)
        candidate.environment_ir = None
        return self._refresh(candidate)

    def record_environment_ir(self, candidate_id: str, environment_ir: EnvironmentIR) -> EnvironmentCandidate:
        if not environment_ir.frozen:
            raise ValueError("environment IR must be frozen before persistence")
        if environment_ir.ir_checksum != environment_ir.semantic_checksum():
            raise ValueError("environment IR checksum does not match frozen semantic content")
        candidate = self.load(candidate_id)
        if environment_ir.environment_id != candidate.environment_id:
            raise ValueError("environment IR environment_id does not match candidate")
        candidate.environment_ir = environment_ir
        evidence_path = self._path(candidate_id).parent / "validation" / "environment-ir.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(environment_ir.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return self.record_check(candidate_id, CandidateCheck(
            check_id="environment_ir", stage="static", status="passed",
            summary="frozen Environment IR persisted", evidence_refs=[str(evidence_path)],
        ))

    def record_rubric_integrity_review(self, candidate_id: str, review: RubricIntegrityReview) -> EnvironmentCandidate:
        """Persist a generation-time rubric alignment review as interface evidence."""
        validation_root = self._path(candidate_id).parent / "validation"
        validation_root.mkdir(parents=True, exist_ok=True)
        review_no = len(list(validation_root.glob("rubric-integrity-review-*.json"))) + 1
        evidence_path = validation_root / f"rubric-integrity-review-{review_no}.json"
        evidence_path.write_text(json.dumps(review.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        status = "passed" if review.verdict == "pass" else ("warning" if review.verdict == "revise" else "failed")
        return self.record_check(candidate_id, CandidateCheck(
            check_id="rubric_integrity", stage="scoring", status=status,
            summary=review.summary, evidence_refs=[str(evidence_path)],
        ))

    def record_scorer_design(self, candidate_id: str, design: ScorerDesign) -> EnvironmentCandidate:
        candidate = self.load(candidate_id)
        candidate.scorer_design = design
        evidence_path = self._path(candidate_id).parent / "validation" / "scorer-design.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(design.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._refresh(candidate)

    def record_scorer_review(self, candidate_id: str, review: ScorerReview) -> EnvironmentCandidate:
        candidate = self.load(candidate_id)
        candidate.scorer_reviews.append(review)
        review_no = len(candidate.scorer_reviews)
        evidence_path = self._path(candidate_id).parent / "validation" / f"scorer-review-{review_no}.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(review.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        candidate = self._refresh(candidate)
        return self.record_check(candidate_id, CandidateCheck(
            check_id="scorer_semantic_review", stage="scoring",
            status="passed" if review.verdict == "pass" else "failed",
            summary=review.summary, evidence_refs=[str(evidence_path)],
        ))

    def record_check(self, candidate_id: str, check: CandidateCheck) -> EnvironmentCandidate:
        candidate = self.load(candidate_id)
        candidate.checks = [existing for existing in candidate.checks if existing.check_id != check.check_id] + [check]
        # Status is derived from the full current check set. Later checks must
        # never mask a semantic blocker recorded earlier in the pipeline.
        failed_checks = {existing.check_id for existing in candidate.checks if existing.status == "failed"}
        if "ir_expressiveness" in failed_checks:
            candidate.status = CandidateStatus.REQUIRES_IR_EXTENSION
        elif failed_checks & {"scenario_contract", "scenario_completeness"}:
            candidate.status = CandidateStatus.SCENARIO_INCOMPLETE
        elif failed_checks:
            candidate.status = CandidateStatus.NEEDS_REPAIR
        elif check.stage == "smoke" and check.status == "passed":
            candidate.status = CandidateStatus.SMOKE_TESTED
        elif check.stage == "scaffold" and check.status == "passed":
            candidate.status = CandidateStatus.SCAFFOLDED
        elif check.stage == "static" and check.status == "passed":
            candidate.status = CandidateStatus.STATIC_VALIDATED
        elif check.stage == "scoring" and check.status == "passed":
            if any(existing.stage == "smoke" and existing.status == "passed" for existing in candidate.checks):
                candidate.status = CandidateStatus.SMOKE_TESTED
            elif any(existing.stage == "scaffold" and existing.status == "passed" for existing in candidate.checks):
                candidate.status = CandidateStatus.SCAFFOLDED
            else:
                candidate.status = CandidateStatus.STATIC_VALIDATED
        return self._refresh(candidate)

    def record_pilot(self, candidate_id: str, trial: PilotTrial) -> EnvironmentCandidate:
        candidate = self.load(candidate_id)
        candidate.pilot_trials.append(trial)
        if candidate.status not in {CandidateStatus.NEEDS_REPAIR, CandidateStatus.REJECTED}:
            candidate.status = CandidateStatus.PILOT_SCORED
        return self._refresh(candidate)

    def record_agent_eval(self, candidate_id: str, report: AgentEvalReportRef) -> EnvironmentCandidate:
        candidate = self.load(candidate_id)
        candidate.agent_eval_reports.append(report)
        return self._refresh(candidate)

    def approve(self, candidate_id: str, approval: HumanApproval) -> EnvironmentCandidate:
        candidate = self.load(candidate_id)
        candidate.human_approval = approval
        candidate.status = CandidateStatus.APPROVED if approval.approved else CandidateStatus.REJECTED
        return self._refresh(candidate)

    def _refresh(self, candidate: EnvironmentCandidate) -> EnvironmentCandidate:
        if candidate.status not in {CandidateStatus.REJECTED, CandidateStatus.NEEDS_REPAIR} and candidate.readiness().ready:
            candidate.status = CandidateStatus.PROMOTION_READY
        self.save(candidate)
        return candidate

    def build_promotion_bundle(self, candidate_id: str) -> Path:
        candidate = self.load(candidate_id)
        readiness = candidate.readiness()
        if not readiness.ready:
            raise ValueError("candidate is not promotion-ready: " + "; ".join(readiness.blockers))
        bundle = self.root / candidate_id / "promotion-bundle.json"
        payload: dict[str, Any] = {
            "schema_version": "benchmark-forge.promotion-bundle.v1",
            "candidate_id": candidate.candidate_id,
            "environment_id": candidate.environment_id,
            "candidate_ref": "candidate.json",
            "target_repository": "agent-octagon-envs",
            "operation": "external reviewed import; Benchmark Forge does not copy automatically",
            "checks": [check.model_dump(mode="json") for check in candidate.checks],
            "pilot_trials": [trial.model_dump(mode="json") for trial in candidate.pilot_trials],
            "agent_eval_reports": [report.model_dump(mode="json") for report in candidate.agent_eval_reports],
            "human_approval": candidate.human_approval.model_dump(mode="json") if candidate.human_approval else None,
        }
        bundle.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return bundle


def stage_generated_candidates(benchmark: Benchmark, registry: EnvironmentCandidateRegistry) -> list[EnvironmentCandidate]:
    candidates = [registry.stage(benchmark, item) for item in benchmark.items if item.item_kind == "executable_task" and item.executable_task and item.executable_task.environment.maturity != "existing"]
    if candidates:
        benchmark.manifest["environment_candidates"] = [
            {"candidate_id": candidate.candidate_id, "status": candidate.status.value, "registry_root": str(registry.root)}
            for candidate in candidates
        ]
    return candidates


class ScaffoldFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str
    purpose: str = ""


class EnvironmentScaffoldBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: str
    files: list[ScaffoldFile]
    implementation_notes: list[str] = Field(default_factory=list)

    def validate_paths(self) -> None:
        required = {"meta.yaml", "core.py", "scorer.py"}
        paths = {file.path for file in self.files}
        if not required <= paths:
            raise ValueError(f"scaffold missing required files: {sorted(required - paths)}")
        if not any(path.startswith("tasks/") and path.endswith(".json") for path in paths):
            raise ValueError("scaffold requires at least one tasks/*.json")
        allowed_roots = {"meta.yaml", "core.py", "scorer.py", "README.md", "mcp_server.py", "schema.sql", "tasks", "inputs", "materials", "schemas", "tests", "private", "blade_skill"}
        for file in self.files:
            path = Path(file.path)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"unsafe scaffold path: {file.path}")
            if path.parts[0] not in allowed_roots:
                raise ValueError(f"unsupported scaffold path: {file.path}")
            if len(file.content.encode("utf-8")) > 512_000:
                raise ValueError(f"scaffold file too large: {file.path}")


class ScaffoldValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def merge_scaffold_bundles(
    base: EnvironmentScaffoldBundle, repair: EnvironmentScaffoldBundle
) -> EnvironmentScaffoldBundle:
    """Overlay an Agent repair without silently deleting unchanged files.

    LLM repair outputs are treated as patches even when prompted to return a
    complete bundle. Explicit deletion is intentionally unsupported in the MVP;
    removing files requires a future typed deletion action and separate review.
    """
    if normalize_environment_id(base.environment_id) != normalize_environment_id(repair.environment_id):
        raise ValueError("repair bundle environment_id does not match base bundle")
    files = {file.path: file for file in base.files}
    files.update({file.path: file for file in repair.files})
    notes = list(base.implementation_notes) + [
        note for note in repair.implementation_notes if note not in base.implementation_notes
    ]
    return EnvironmentScaffoldBundle(
        environment_id=normalize_environment_id(base.environment_id),
        files=list(files.values()),
        implementation_notes=notes,
    )


def validate_contract_realizability(item: BenchmarkItem) -> list[str]:
    """Reject data-dependent contracts without an executable scenario binding."""
    task = item.executable_task
    if task is None:
        return []
    environment = task.environment
    errors: list[str] = []
    material_ids = {material.material_id for material in environment.materials}
    scenario = environment.scenario
    if scenario is not None:
        unknown = set(scenario.material_refs) - material_ids
        if unknown:
            errors.append(f"scenario references unknown materials: {sorted(unknown)}")
        bindings = bool(scenario.material_refs or scenario.runtime_generator_ref or scenario.evaluation_injection_ref)
        if scenario.data_dependent and not bindings:
            errors.append("data-dependent scenario requires material_refs, runtime_generator_ref, or evaluation_injection_ref")
        if not scenario.allow_empty and scenario.minimum_items < 1:
            errors.append("non-empty scenario requires minimum_items >= 1")
    # Legacy-contract guard: generated data-access tasks must not rely on an
    # unbound implementation_ref promise. This is intentionally conservative
    # and applies only when both task language and tool names indicate data.
    instruction = task.instruction.lower()
    data_language = any(token in instruction for token in (
        "record", "document", "material", "dataset", "资料", "记录", "文档", "数据",
    ))
    data_tools = any(
        any(token in tool.name.lower() for token in ("list", "get", "read", "search", "fetch", "query"))
        for tool in environment.tools if tool.ownership == "benchmark_environment"
    )
    if (
        scenario is None and not environment.materials
        and environment.maturity in {"generated_contract", "pending"}
        and environment.implementation is not None and environment.implementation.type == "generated"
        and data_language and data_tools
    ):
        errors.append(
            "generated data-dependent task has no MaterialContract or typed scenario generator/injection binding"
        )
    if len(material_ids) != len(environment.materials):
        errors.append("duplicate material_id in environment contract")
    return errors


def validate_agent_subject_contract(item: BenchmarkItem) -> list[str]:
    """Reject contracts that replace the evaluated Agent capability with a simulator."""
    task = item.executable_task
    if task is None:
        return []
    errors: list[str] = []
    capability_names = {cap.name for cap in task.agent_capabilities}
    simulated_native = sorted(
        tool.name for tool in task.environment.tools
        if tool.name in capability_names and tool.ownership == "benchmark_environment"
        and tool.name not in {"tool_use"}
    )
    if simulated_native:
        errors.append(
            "benchmark environment reimplements Agent-native capabilities as synthetic tools: "
            f"{simulated_native}; bind them to agent_runtime and observe host trajectory evidence"
        )
    measured_dimensions = {item.dimension_id, *item.covered_dimension_ids}
    measures_native_context = any(
        "context_compression" in dimension or "memory" in dimension
        for dimension in measured_dimensions
    )
    synthetic_memory = sorted(
        tool.name for tool in task.environment.tools
        if tool.ownership == "benchmark_environment"
        and tool.name in {"memory_write", "memory_read", "context_compress", "context_restore"}
    )
    if measures_native_context and synthetic_memory:
        errors.append(
            "benchmark environment substitutes synthetic memory/context tools for the Agent capability being measured: "
            f"{synthetic_memory}; trigger/observe native context management through the host evaluation trace instead"
        )
    measures_delegation = any("delegation" in dimension for dimension in measured_dimensions)
    agent_controlled_injectors = sorted(
        tool.name for tool in task.environment.tools
        if tool.ownership == "benchmark_environment"
        and any(token in tool.name.lower() for token in ("injector", "inject_fault", "fault_injection"))
    )
    if measures_delegation and agent_controlled_injectors:
        errors.append(
            "delegation perturbation injectors must be controlled by evaluation_system, not callable by the Agent: "
            f"{agent_controlled_injectors}"
        )
    return errors


def validate_scaffold(
    bundle: EnvironmentScaffoldBundle, item: BenchmarkItem, ir: EnvironmentIR | None = None,
) -> ScaffoldValidation:
    import ast
    import re
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        return ScaffoldValidation(valid=False, errors=[f"PyYAML unavailable: {exc}"])
    errors: list[str] = []
    warnings: list[str] = []
    try:
        bundle.validate_paths()
    except ValueError as exc:
        errors.append(str(exc))
    files = {file.path: file.content for file in bundle.files}
    meta: dict[str, Any] = {}
    try:
        loaded = yaml.safe_load(files.get("meta.yaml", ""))
        meta = loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        errors.append(f"meta.yaml invalid: {exc}")
    if meta.get("name") != bundle.environment_id:
        errors.append("meta.yaml name must equal environment_id")
    expected_dims = {dimension.name for dimension in (item.executable_task.scoring.dimensions if item.executable_task else [])}
    actual_dims = {str(d.get("name")) for d in meta.get("dimensions", []) if isinstance(d, dict)}
    if expected_dims != actual_dims:
        errors.append(f"meta dimensions mismatch: expected={sorted(expected_dims)}, actual={sorted(actual_dims)}")
    for path in ("core.py", "scorer.py"):
        try:
            tree = ast.parse(files.get(path, ""), filename=path)
            if path == "scorer.py" and not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "score" for node in tree.body):
                errors.append("scorer.py must define score")
        except SyntaxError as exc:
            errors.append(f"{path} syntax error: {exc}")
    task_paths = [path for path in files if path.startswith("tasks/") and path.endswith(".json")]
    for path in task_paths:
        try:
            task = json.loads(files[path])
        except json.JSONDecodeError as exc:
            errors.append(f"{path} invalid JSON: {exc}")
            continue
        for field_name in ("id", "env_name", "prompt", "timeout_seconds"):
            if not task.get(field_name):
                errors.append(f"{path} missing {field_name}")
        if task.get("env_name") != bundle.environment_id:
            errors.append(f"{path} env_name mismatch")
    # Flat bundle protocol: metadata must point at a module that linker owns.
    entrypoints = meta.get("entrypoints") if isinstance(meta, dict) else {}
    if isinstance(entrypoints, dict):
        for name, config in entrypoints.items():
            command = config.get("command") if isinstance(config, dict) else None
            if isinstance(command, list):
                for value in command:
                    if isinstance(value, str) and value.endswith(".mcp_server"):
                        package_path = f"{value.rsplit('.', 1)[0]}/mcp_server.py"
                        if package_path not in files:
                            errors.append(f"entrypoint {name} references missing package module {value}; flat bundle requires mcp_server.py")
    # Construct and scenario validity are separate from file syntax.
    errors.extend(validate_agent_subject_contract(item))
    errors.extend(validate_contract_realizability(item))
    if item.executable_task:
        runtime_source = files.get("core.py", "") + "\n" + files.get("mcp_server.py", "")
        implemented_native = sorted(
            tool.name for tool in item.executable_task.environment.tools
            if tool.ownership == "agent_runtime"
            and re.search(rf"def\s+_?{re.escape(tool.name)}\s*\(", runtime_source)
        )
        if implemented_native:
            errors.append(
                "generated runtime implements tools owned by agent_runtime: "
                f"{implemented_native}; native capabilities must come from the evaluated Agent"
            )

    # Generated tests must be runnable before an agent has produced artifacts.
    # A test that asserts Path("artifacts/...").is_file() is an attempt-time
    # assertion and makes a clean bundle fail its own static test suite.
    for path, content in files.items():
        if path.startswith("tests/") and re.search(r"(?:Path|pathlib\.Path)\(\s*[\"']artifacts/", content) and ".is_file()" in content:
            errors.append(f"{path} requires pre-existing runtime artifacts; tests must create temporary fixtures")
        if path.startswith("tests/") and ("read_text" in content or "inspect.getsource" in content) and re.search(r"assert\s+[\"'][^\"']+[\"']\s+in\s+source|assert\s+[^\n]+\s+in\s+source", content):
            errors.append(f"{path} asserts implementation source text instead of public runtime behavior")

    if item.executable_task:
        environment = item.executable_task.environment
        scenario = environment.scenario
        observed_items = 0
        observed_tags: set[str] = set()
        for material in environment.materials:
            if not material.required or material.visibility != "agent":
                continue
            matching_paths = [path for path in files if path == material.target or path.startswith(material.target.rstrip("/") + "/")]
            if environment.maturity in {"generated_contract", "pending"} and material.source.type == "generated" and not matching_paths:
                errors.append(f"required generated material missing from bundle: {material.material_id} -> {material.target}")
                continue
            if material.minimum_items is not None and matching_paths:
                target_path = material.target if material.target in files else matching_paths[0]
                try:
                    payload = json.loads(files[target_path])
                    collection = payload.get(material.collection_key) if material.collection_key and isinstance(payload, dict) else payload
                    count = len(collection) if isinstance(collection, list) else 0
                    observed_items = max(observed_items, count)
                    if count < material.minimum_items:
                        errors.append(f"material {material.material_id} has {count} items; minimum is {material.minimum_items}")
                    if scenario and scenario.case_tag_field and isinstance(collection, list):
                        for entry in collection:
                            if isinstance(entry, dict):
                                tags = entry.get(scenario.case_tag_field)
                                if isinstance(tags, str): observed_tags.add(tags)
                                elif isinstance(tags, list): observed_tags.update(str(tag) for tag in tags)
                except Exception as exc:
                    errors.append(f"material {material.material_id} cannot be validated as JSON collection: {exc}")
        if scenario and scenario.data_dependent and not scenario.allow_empty:
            externally_bound = bool(scenario.runtime_generator_ref or scenario.evaluation_injection_ref)
            if not externally_bound and observed_items < scenario.minimum_items:
                errors.append(f"scenario has {observed_items} observable items; minimum is {scenario.minimum_items}")
            missing_tags = set(scenario.required_case_tags) - observed_tags
            if scenario.required_case_tags and not externally_bound and missing_tags:
                errors.append(f"scenario missing required case tags: {sorted(missing_tags)}")

    if ir is not None:
        errors.extend(validate_bundle_abi(meta=meta, files=files, ir=ir))

    scorer_content = files.get("scorer.py", "")
    try:
        scorer_tree = ast.parse(scorer_content, filename="scorer.py")
        loaded_names = {node.id for node in ast.walk(scorer_tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        if not ({"attempt_id", "env_db", "trace", "final_state"} & loaded_names):
            warnings.append(
                "scorer.py may not consume any canonical attempt evidence input "
                "(attempt_id/env_db/trace/final_state); semantic review required"
            )
    except SyntaxError:
        pass
    if item.executable_task and item.executable_task.environment.scenario and item.executable_task.environment.scenario.data_dependent:
        if "invalid_environment" not in scorer_content:
            errors.append("data-dependent scorer must expose invalid_environment handling for missing/insufficient scenario evidence")

    public_prompt = " ".join(
        json.loads(files[path]).get("prompt", "")
        for path in task_paths
        if path in files
    )
    if ir is not None and "artifact_schema" in ir.required_features:
        meta_artifacts = {
            str(entry.get("artifact_id")): entry
            for entry in meta.get("artifacts", [])
            if isinstance(entry, dict) and entry.get("artifact_id")
        }
        for artifact in ir.artifacts:
            if not artifact.schema_path:
                continue
            declared = meta_artifacts.get(artifact.artifact_id)
            if not declared:
                errors.append(f"meta.yaml missing typed artifact {artifact.artifact_id}")
            else:
                if declared.get("schema_path") != artifact.schema_path:
                    errors.append(f"meta artifact {artifact.artifact_id} schema_path does not match Frozen IR")
                if declared.get("schema_def") != artifact.schema_def:
                    errors.append(f"meta artifact {artifact.artifact_id} schema_def does not match Frozen IR")
            schema_source = files.get(artifact.schema_path)
            if schema_source is None:
                errors.append(f"canonical artifact schema missing from bundle: {artifact.schema_path}")
            else:
                try:
                    schema_payload = json.loads(schema_source)
                    if schema_payload != artifact.schema_def:
                        errors.append(f"artifact schema file does not equal Frozen IR schema_def: {artifact.schema_path}")
                except json.JSONDecodeError as exc:
                    errors.append(f"artifact schema file is invalid JSON: {artifact.schema_path}: {exc}")
            if artifact.schema_path not in public_prompt:
                errors.append(f"public task prompt does not name artifact schema_path: {artifact.schema_path}")
    coordination = item.executable_task.coordination if item.executable_task else None
    if coordination:
        hidden_required_ids = [
            subtask.subtask_id for subtask in coordination.subtasks
            if subtask.subtask_id in scorer_content and subtask.subtask_id not in public_prompt
        ]
        if hidden_required_ids:
            errors.append(f"scorer requires hidden coordination node IDs not found in the public prompt: {hidden_required_ids}")
    forbidden_patterns = ["/home/yang", "sk-llm", "LLM_API_KEY", "OPENAI_API_KEY"]
    for path, content in files.items():
        for pattern in forbidden_patterns:
            if pattern in content:
                errors.append(f"{path} contains forbidden host/credential reference")
    if "README.md" not in files:
        warnings.append("README.md missing")
    if not any(path.startswith("tests/") for path in files):
        warnings.append("tests/ missing; smoke coverage is weak")
    return ScaffoldValidation(valid=not errors, errors=errors, warnings=warnings)


def write_scaffold(registry: EnvironmentCandidateRegistry, candidate_id: str, bundle: EnvironmentScaffoldBundle) -> tuple[Path, ScaffoldValidation]:
    candidate = registry.load(candidate_id)
    normalized_bundle_id = normalize_environment_id(bundle.environment_id)
    normalized_candidate_id = normalize_environment_id(candidate.environment_id)
    bundle = bundle.model_copy(update={"environment_id": normalized_bundle_id})
    if normalized_bundle_id != normalized_candidate_id:
        raise ValueError("bundle environment_id does not match candidate")
    if candidate.environment_id != normalized_candidate_id:
        candidate.environment_id = normalized_candidate_id
        if candidate.item.executable_task is not None:
            candidate.item.executable_task.environment.environment_id = normalized_candidate_id
        registry.save(candidate)
    bundle = normalize_octagon_scaffold(bundle, candidate.item, candidate.environment_ir)
    validation = validate_scaffold(bundle, candidate.item, candidate.environment_ir)
    safe_env_id = bundle.environment_id
    if Path(safe_env_id).name != safe_env_id or safe_env_id in {".", ".."}:
        raise ValueError("unsafe environment_id")
    root = registry._path(candidate_id).parent / "scaffold" / safe_env_id
    root.mkdir(parents=True, exist_ok=True)
    for file in bundle.files:
        destination = root / file.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(file.content, encoding="utf-8")
    (root / "scaffold-manifest.json").write_text(json.dumps({
        "environment_id": bundle.environment_id,
        "files": [{"path": file.path, "purpose": file.purpose} for file in bundle.files],
        "implementation_notes": bundle.implementation_notes,
        "static_validation": validation.model_dump(mode="json"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    scenario_markers = (
        "material", "scenario has", "case tags", "invalid_environment", "scenario dependencies",
    )
    scenario_errors = [
        error for error in validation.errors
        if any(marker in error.lower() for marker in scenario_markers)
    ]
    registry.record_check(candidate_id, CandidateCheck(
        check_id="scenario_completeness", stage="static",
        status="failed" if scenario_errors else "passed",
        summary="; ".join(scenario_errors) if scenario_errors else "materialized scenario satisfies typed completeness requirements",
        evidence_refs=[str(root / "scaffold-manifest.json")],
    ))
    registry.record_check(candidate_id, CandidateCheck(
        check_id="contract_schema", stage="static", status="passed" if validation.valid else "failed",
        summary="environment file contract and schemas validated" if validation.valid else "; ".join(validation.errors),
        evidence_refs=[str(root / "scaffold-manifest.json")],
    ))
    abi_errors = [error for error in validation.errors if "AgentOctagon" in error]
    registry.record_check(candidate_id, CandidateCheck(
        check_id="agent_octagon_abi", stage="static", status="failed" if abi_errors else "passed",
        summary="; ".join(abi_errors) if abi_errors else "bundle matches current AgentOctagon loader/material/tool/scorer ABI",
        evidence_refs=[str(root / "scaffold-manifest.json")],
    ))
    registry.record_check(candidate_id, CandidateCheck(
        check_id="provenance_safety", stage="static", status="passed" if not any("forbidden" in error for error in validation.errors) else "failed",
        summary="no host path or credential leakage found",
        evidence_refs=[str(root / "scaffold-manifest.json")],
    ))
    registry.record_check(candidate_id, CandidateCheck(
        check_id="scaffold_integrity", stage="scaffold", status="passed" if validation.valid else "failed",
        summary="required Octagon files generated" if validation.valid else "scaffold has static validation failures",
        evidence_refs=[str(root)],
    ))
    return root, validation


def _normalize_flat_mcp_entrypoint(entrypoints: Any, file_paths: set[str]) -> Any:
    """Make generated flat bundles executable without an invented package.

    Component ownership emits ``mcp_server.py`` at the bundle root. LLMs often
    copy a package-style ``python -m foo.mcp_server`` into metadata/tests even
    though no ``foo/`` directory is allowed by the ownership contract.
    """
    if not isinstance(entrypoints, dict):
        return entrypoints
    result = json.loads(json.dumps(entrypoints))
    commands = []
    for value in result.values():
        if isinstance(value, dict) and isinstance(value.get("command"), list):
            commands.append(value["command"])
    for command in commands:
        for index, value in enumerate(command):
            if isinstance(value, str) and value.endswith(".mcp_server") and f"{value.rsplit('.', 1)[0]}/mcp_server.py" not in file_paths:
                command[index] = "mcp_server"
    return result


def normalize_octagon_scaffold(bundle: EnvironmentScaffoldBundle, item: BenchmarkItem, ir: EnvironmentIR | None = None) -> EnvironmentScaffoldBundle:
    """Deterministically normalize model output to the canonical Octagon loader shape."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return bundle
    files = {file.path: file for file in bundle.files}
    task = item.executable_task
    assert task is not None
    raw_meta = yaml.safe_load(files.get("meta.yaml", ScaffoldFile(path="meta.yaml", content="")).content) or {}
    if ir is not None:
        evidence_by_id = {e.evidence_id: e.source_type for e in ir.evidence}
        canonical_dimensions = [{
            "name": criterion.criterion_id,
            "weight": criterion.weight,
            "description": criterion.description,
            "minimum_score": criterion.minimum_score,
            "critical_gate": criterion.critical_gate,
            "evidence_sources": sorted({evidence_by_id[ref] for ref in criterion.evidence_refs if ref in evidence_by_id}),
        } for criterion in ir.rubric.criteria]
        canonical_tools = [
            tool.model_dump(mode="json") for tool in ir.tools
            if tool.ownership == "benchmark_environment"
        ]
        canonical_artifacts = [artifact.model_dump(mode="json") for artifact in ir.artifacts]
        canonical_materials = [material.model_dump(mode="json") for material in ir.materials]
    else:
        canonical_dimensions = raw_meta.get("dimensions") or [dimension.model_dump(mode="json") for dimension in task.scoring.dimensions]
        canonical_tools = raw_meta.get("tools") or [
            tool.model_dump(mode="json") for tool in task.environment.tools
            if tool.ownership == "benchmark_environment"
        ]
        canonical_artifacts = raw_meta.get("artifacts") or [artifact.model_dump(mode="json") for artifact in task.artifacts]
        canonical_materials = [material.model_dump(mode="json") for material in task.environment.materials]
    if ir is not None:
        canonical_material_mounts = material_mounts(ir, set(files))
        canonical_material_contracts = canonical_materials
        canonical_entrypoints = (
            mcp_entrypoint(bundle.environment_id) if canonical_tools
            else _normalize_flat_mcp_entrypoint(raw_meta.get("entrypoints") or task.environment.entrypoints, set(files))
        )
    else:
        canonical_material_mounts = raw_meta.get("materials") or {}
        canonical_material_contracts = canonical_materials
        canonical_entrypoints = _normalize_flat_mcp_entrypoint(
            raw_meta.get("entrypoints") or task.environment.entrypoints, set(files)
        )
    prerequisites = raw_meta.get("prerequisites") or task.environment.prerequisites or {
        "level": "none", "summary": "Generated candidate prerequisites", "requires": [], "on_missing": "block pilot",
    }
    if canonical_tools and isinstance(prerequisites, dict):
        prerequisites = dict(prerequisites)
        prerequisites["summary"] = (
            "No public internet or third-party service is required; AgentOctagon must provide "
            "the authenticated local MCP attempt-tool runtime declared by entrypoints.mcp."
        )
        requires = list(prerequisites.get("requires") or [])
        if "AgentOctagon authenticated MCP runtime" not in requires:
            requires.append("AgentOctagon authenticated MCP runtime")
        prerequisites["requires"] = requires
        prerequisites["on_missing"] = "infrastructure failure; do not score Agent capability"

    canonical_meta = {
        "name": bundle.environment_id,
        "schema_version": "1.0",
        "type": raw_meta.get("type") or raw_meta.get("environment_type") or task.environment.environment_type,
        "category": raw_meta.get("category") or "agent-system",
        "test_focus": raw_meta.get("test_focus") or task.instruction[:300],
        "description": raw_meta.get("description") or "Generated executable environment candidate; not canonical until promoted.",
        "pass_threshold": raw_meta.get("pass_threshold", task.scoring.pass_threshold or 60),
        "prerequisites": prerequisites,
        "task_id": task.task_id,
        "protocol": ir.protocol if ir is not None else task.environment.protocol,
        "entrypoints": canonical_entrypoints,
        "tools": canonical_tools,
        "artifacts": canonical_artifacts,
        "materials": canonical_material_mounts,
        "material_contracts": canonical_material_contracts,
        "runtime_abi": "agent-octagon.env-loader.v1",
        "dimensions": canonical_dimensions,
    }
    files["meta.yaml"] = ScaffoldFile(path="meta.yaml", content=yaml.safe_dump(canonical_meta, allow_unicode=True, sort_keys=False), purpose="canonical Octagon metadata")
    for path, file in list(files.items()):
        if path.startswith("tasks/") and path.endswith(".json"):
            try:
                data = json.loads(file.content)
            except json.JSONDecodeError:
                continue
            data["env_name"] = bundle.environment_id
            data.setdefault("id", Path(path).stem)
            data.setdefault("prompt", task.instruction)
            data.setdefault("timeout_seconds", task.environment.timeout_seconds)
            files[path] = file.model_copy(update={"content": json.dumps(data, ensure_ascii=False, indent=2)})
    # Tests are part of the generated executable contract. Keep their import
    # target consistent with the flat runtime ownership shape.
    for path, file in list(files.items()):
        if path.startswith("tests/") and path.endswith(".py") and "mcp_server.py" in files:
            import re
            content = re.sub(r"([A-Za-z_][A-Za-z0-9_]*)\.mcp_server", "mcp_server", file.content)
            if content != file.content:
                files[path] = file.model_copy(update={"content": content})
    return bundle.model_copy(update={"files": list(files.values())})
