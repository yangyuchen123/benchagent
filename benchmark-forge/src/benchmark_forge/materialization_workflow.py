from __future__ import annotations

"""Resumable Contract -> IR -> component -> linker workflow.

This module deliberately does not regenerate Design, Grounding, Allocation, or
an ExecutableTaskContract.  It automates the part that was previously performed
by an engineer/Codex after a contract had already been accepted: isolate the
failing compiler/component boundary, repair only that boundary, relink, and
persist enough evidence to resume without repeating successful model calls.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .domain import BenchmarkItem
from .environment_ir import (
    EnvironmentIR,
    IRComponentOutput,
    IRExpressivenessError,
    IRValidationError,
    link_component_outputs,
)
from .staging import (
    EnvironmentScaffoldBundle, ScaffoldValidation, normalize_octagon_scaffold,
    validate_contract_realizability, validate_scaffold,
)


ComponentId = Literal["manifest", "runtime", "scorer", "tests"]
_COMPONENT_ORDER: tuple[ComponentId, ...] = ("manifest", "runtime", "scorer", "tests")


class WorkflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: Literal["contract", "ir", "component", "link", "static_validation", "bundle_tests"]
    action: str
    status: Literal["started", "passed", "failed", "reused", "repaired", "skipped"]
    target: str | None = None
    duration_seconds: float = 0
    details: list[str] = Field(default_factory=list)


class FailureObservation(BaseModel):
    """Bounded failure evidence supplied to deterministic/Agent diagnosis."""

    model_config = ConfigDict(extra="forbid")
    failure_kind: Literal["component_generation", "link", "static_validation", "bundle_tests"]
    summary: str
    errors: list[str] = Field(default_factory=list)
    candidate_components: list[ComponentId] = Field(default_factory=list)
    test_output: str = ""


class RepairPlan(BaseModel):
    """A diagnosis result cannot modify Contract or Frozen IR."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["repair_components", "retry_generation", "stop"]
    component_ids: list[ComponentId] = Field(default_factory=list)
    rationale: str
    repair_instructions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)


class BundleTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    returncode: int
    output: str = ""
    failed_test_paths: list[str] = Field(default_factory=list)
    duration_seconds: float = 0


class MaterializationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_calls: int = 0
    ir_compile_attempts: int = 0
    reused_ir: int = 0
    reused_components: int = 0
    generated_components: int = 0
    repaired_components: int = 0
    linker_attempts: int = 0
    static_validation_attempts: int = 0
    bundle_test_attempts: int = 0
    automatic_diagnoses: int = 0
    agent_diagnoses: int = 0
    manual_interventions: int = 0


class MaterializationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "benchmark-forge.materialization-workflow.v1"
    run_id: str = Field(default_factory=lambda: f"mat-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}")
    status: Literal["ready", "needs_repair", "scenario_incomplete", "requires_ir_extension", "failed"]
    task_id: str
    environment_id: str
    contract_checksum: str
    ir_checksum: str | None = None
    bundle_root: str | None = None
    manual_intervention_required: bool = False
    stop_reason: str = ""
    validation: ScaffoldValidation | None = None
    bundle_tests: BundleTestResult | None = None
    metrics: MaterializationMetrics = Field(default_factory=MaterializationMetrics)
    events: list[WorkflowEvent] = Field(default_factory=list)


@dataclass(frozen=True)
class MaterializationPolicy:
    """Budgets apply below the frozen Contract/IR boundary only."""

    max_generation_attempts_per_component: int = 2
    max_repairs_per_component: int = 2
    max_link_cycles: int = 5
    run_bundle_tests: bool = False


class ComponentGenerator(Protocol):
    def __call__(
        self, *, component_id: ComponentId, item: BenchmarkItem, ir: EnvironmentIR,
        dependency_outputs: list[IRComponentOutput],
    ) -> IRComponentOutput: ...


class ComponentRepairer(Protocol):
    def __call__(
        self, *, component_id: ComponentId, item: BenchmarkItem, ir: EnvironmentIR,
        current: IRComponentOutput, review: FailureObservation | RepairPlan,
        dependency_outputs: list[IRComponentOutput],
    ) -> IRComponentOutput: ...


class FailureDiagnoser(Protocol):
    def __call__(
        self, *, item: BenchmarkItem, ir: EnvironmentIR,
        outputs: list[IRComponentOutput], observation: FailureObservation,
    ) -> RepairPlan: ...


class BundleTestBackend(Protocol):
    def run(self, bundle_root: Path) -> BundleTestResult: ...


class LocalPytestBackend:
    """Development-only backend.

    Production should execute generated code in eval-system isolation and feed
    its versioned result artifact back to this workflow.  This backend exists
    for local Forge self-tests and is opt-in because generated code is untrusted.
    """

    def __init__(self, timeout_seconds: float = 60.0):
        self.timeout_seconds = timeout_seconds

    def run(self, bundle_root: Path) -> BundleTestResult:
        started = time.monotonic()
        try:
            safe_env = {
                key: value for key, value in os.environ.items()
                if not any(token in key.upper() for token in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            }
            safe_env["PYTHONPATH"] = str(bundle_root)
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"], cwd=bundle_root,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=self.timeout_seconds, check=False, env=safe_env,
            )
            output = completed.stdout[-20_000:]
            # Warning summaries also contain ``tests/foo.py::test_name``.  They
            # are not failures and must not pollute a successful run report.
            failed_paths = (
                sorted(set(re.findall(r"^FAILED\s+(tests/[^:\s]+\.py)", output, re.MULTILINE)))
                if completed.returncode != 0
                else []
            )
            self._clean_test_caches(bundle_root)
            return BundleTestResult(
                passed=completed.returncode == 0, returncode=completed.returncode,
                output=output, failed_test_paths=failed_paths,
                duration_seconds=round(time.monotonic() - started, 3),
            )
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or "") + "\n" + (exc.stderr or ""))[-20_000:]
            self._clean_test_caches(bundle_root)
            return BundleTestResult(
                passed=False, returncode=124, output=f"bundle tests timed out\n{output}",
                duration_seconds=round(time.monotonic() - started, 3),
            )

    @staticmethod
    def _clean_test_caches(bundle_root: Path) -> None:
        for path in bundle_root.rglob("__pycache__"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(bundle_root / ".pytest_cache", ignore_errors=True)


def _checksum(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _component_dependencies(ir: EnvironmentIR, outputs: dict[str, IRComponentOutput], component_id: str) -> list[IRComponentOutput]:
    spec = next(component for component in ir.components if component.component_id == component_id)
    return [outputs[dep] for dep in spec.depends_on if dep in outputs]


def _static_error_component(error: str) -> ComponentId | None:
    lower = error.lower()
    if "tests/" in lower or "test " in lower or "test_" in lower:
        return "tests"
    if "scorer.py" in lower or "scorer " in lower or "scoring" in lower or "coordination node ids" in lower:
        return "scorer"
    if ("entrypoint" in lower or "meta.yaml" in lower or "task json" in lower or "tasks/" in lower
            or "env_name" in lower or "material" in lower or "scenario has" in lower or "case tags" in lower):
        return "manifest"
    if "core.py" in lower or "mcp_server" in lower or "runtime" in lower or "implements tools owned by agent_runtime" in lower:
        return "runtime"
    return None


def deterministic_repair_plan(observation: FailureObservation) -> RepairPlan | None:
    """Return a plan only when ownership is mechanically unambiguous."""
    candidates: list[ComponentId] = list(observation.candidate_components)
    if observation.failure_kind == "link":
        for error in observation.errors:
            match = re.search(r"component (manifest|runtime|scorer|tests) does not own path", error)
            if match:
                candidates.append(match.group(1))  # type: ignore[arg-type]
            match = re.search(r"path collision: .* \((manifest|runtime|scorer|tests) / (manifest|runtime|scorer|tests)\)", error)
            if match:
                candidates.extend([match.group(1), match.group(2)])  # type: ignore[list-item]
    elif observation.failure_kind == "static_validation":
        for error in observation.errors:
            owner = _static_error_component(error)
            if owner is not None:
                candidates.append(owner)
    elif observation.failure_kind == "bundle_tests":
        # A failing generated test is not automatically a Tests defect: it can
        # correctly expose Runtime or Scorer behavior. Leave ambiguous failures
        # to the bounded diagnosis Agent.
        return None
    unique = [component for component in _COMPONENT_ORDER if component in candidates]
    if not unique:
        return None
    return RepairPlan(
        action="repair_components", component_ids=unique,
        rationale="failure ownership was determined from linker/static protocol paths",
        repair_instructions=observation.errors,
        confidence=1.0,
    )


class MaterializationWorkflow:
    """Resumable, fixed-semantics materialization controller.

    The workflow may retry structured output and repair component files, but it
    never asks an Agent to rewrite the accepted Contract or Frozen IR.  If it
    cannot localize a failure inside those boundaries, it stops with an
    auditable report instead of silently simplifying the benchmark.
    """

    def __init__(
        self, *, component_generator: ComponentGenerator,
        component_repairer: ComponentRepairer | None = None,
        diagnoser: FailureDiagnoser | None = None,
        policy: MaterializationPolicy | None = None,
        test_backend: BundleTestBackend | None = None,
    ):
        self.component_generator = component_generator
        self.component_repairer = component_repairer
        self.diagnoser = diagnoser
        self.policy = policy or MaterializationPolicy()
        self.test_backend = test_backend

    def run_components(
        self, *, item: BenchmarkItem, ir: EnvironmentIR, output_root: str | Path,
    ) -> tuple[EnvironmentScaffoldBundle | None, MaterializationReport]:
        if item.executable_task is None:
            raise ValueError("materialization requires executable_task")
        if not ir.frozen or ir.ir_checksum != ir.semantic_checksum():
            raise ValueError("materialization requires a checksum-valid Frozen IR")
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        contract_checksum = _checksum(item.executable_task)
        report = MaterializationReport(
            status="failed", task_id=ir.task_id, environment_id=ir.environment_id,
            contract_checksum=contract_checksum, ir_checksum=ir.ir_checksum,
        )
        outputs: dict[str, IRComponentOutput] = {}
        repair_counts = {component: 0 for component in _COMPONENT_ORDER}

        def event(stage: str, action: str, status: str, *, target: str | None = None,
                  started: float | None = None, details: list[str] | None = None) -> None:
            report.events.append(WorkflowEvent(
                sequence=len(report.events) + 1, stage=stage, action=action, status=status,
                target=target, duration_seconds=round(time.monotonic() - started, 3) if started else 0,
                details=(details or [])[:50],
            ))
            self._persist_report(root, report)

        # Successful component outputs are reusable only under the exact same
        # Contract and Frozen IR checksums.
        for component_id in _COMPONENT_ORDER:
            cached = self._load_component(root, component_id, contract_checksum, ir.ir_checksum or "")
            if cached is not None:
                outputs[component_id] = cached
                report.metrics.reused_components += 1
                event("component", "load_checkpoint", "reused", target=component_id)
                continue
            last_error = ""
            for attempt in range(1, self.policy.max_generation_attempts_per_component + 1):
                started = time.monotonic()
                try:
                    report.metrics.model_calls += 1
                    output = self.component_generator(
                        component_id=component_id, item=item, ir=ir,
                        dependency_outputs=_component_dependencies(ir, outputs, component_id),
                    )
                    if output.component_id != component_id:
                        raise ValueError(f"returned component_id={output.component_id}")
                    outputs[component_id] = output
                    self._save_component(root, output, contract_checksum, ir.ir_checksum or "")
                    report.metrics.generated_components += 1
                    event("component", f"generate_attempt_{attempt}", "passed", target=component_id, started=started)
                    break
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    event("component", f"generate_attempt_{attempt}", "failed", target=component_id,
                          started=started, details=[last_error])
            else:
                report.status = "needs_repair"
                report.manual_intervention_required = True
                report.metrics.manual_interventions += 1
                report.stop_reason = f"component generation exhausted for {component_id}: {last_error}"
                self._persist_report(root, report)
                return None, report

        bundle: EnvironmentScaffoldBundle | None = None
        for _cycle in range(1, self.policy.max_link_cycles + 1):
            report.metrics.linker_attempts += 1
            started = time.monotonic()
            try:
                bundle = link_component_outputs(ir, [outputs[c] for c in _COMPONENT_ORDER])
                bundle = normalize_octagon_scaffold(bundle, item, ir)
                event("link", "link_components", "passed", started=started)
            except IRValidationError as exc:
                error = str(exc)
                event("link", "link_components", "failed", started=started, details=[error])
                observation = FailureObservation(failure_kind="link", summary=error, errors=[error])
                if not self._apply_repairs(item, ir, outputs, observation, repair_counts, report, root, event):
                    report.status = "needs_repair"
                    report.stop_reason = error
                    self._mark_manual(report)
                    self._persist_report(root, report)
                    return None, report
                continue

            report.metrics.static_validation_attempts += 1
            validation = validate_scaffold(bundle, item, ir)
            report.validation = validation
            event("static_validation", "validate_scaffold", "passed" if validation.valid else "failed",
                  details=validation.errors + validation.warnings)
            if not validation.valid:
                observation = FailureObservation(
                    failure_kind="static_validation", summary="; ".join(validation.errors),
                    errors=validation.errors,
                )
                if not self._apply_repairs(item, ir, outputs, observation, repair_counts, report, root, event):
                    report.status = "needs_repair"
                    report.stop_reason = observation.summary
                    self._mark_manual(report)
                    self._persist_report(root, report)
                    return bundle, report
                continue

            bundle_root = root / "bundle"
            self._write_bundle(bundle_root, bundle)
            report.bundle_root = str(bundle_root)
            if self.policy.run_bundle_tests:
                if self.test_backend is None:
                    report.status = "failed"
                    report.stop_reason = "run_bundle_tests enabled without a test backend"
                    self._mark_manual(report)
                    self._persist_report(root, report)
                    return bundle, report
                report.metrics.bundle_test_attempts += 1
                test_result = self.test_backend.run(bundle_root)
                report.bundle_tests = test_result
                event("bundle_tests", "run_tests", "passed" if test_result.passed else "failed",
                      details=[test_result.output[-8_000:]])
                if not test_result.passed:
                    observation = FailureObservation(
                        failure_kind="bundle_tests", summary="generated bundle tests failed",
                        errors=[f"returncode={test_result.returncode}", *test_result.failed_test_paths],
                        test_output=test_result.output[-12_000:],
                    )
                    if not self._apply_repairs(item, ir, outputs, observation, repair_counts, report, root, event):
                        report.status = "needs_repair"
                        report.stop_reason = "bundle tests failed and ownership could not be repaired automatically"
                        self._mark_manual(report)
                        self._persist_report(root, report)
                        return bundle, report
                    continue
            report.status = "ready"
            report.manual_intervention_required = False
            report.stop_reason = ""
            self._persist_report(root, report)
            return bundle, report

        report.status = "needs_repair"
        report.stop_reason = f"materialization exceeded {self.policy.max_link_cycles} link/validation cycles"
        self._mark_manual(report)
        self._persist_report(root, report)
        return bundle, report

    def _apply_repairs(
        self, item: BenchmarkItem, ir: EnvironmentIR, outputs: dict[str, IRComponentOutput],
        observation: FailureObservation, repair_counts: dict[str, int], report: MaterializationReport,
        root: Path, event: Callable[..., None],
    ) -> bool:
        plan = deterministic_repair_plan(observation)
        if plan is not None:
            report.metrics.automatic_diagnoses += 1
        elif self.diagnoser is not None:
            started = time.monotonic()
            try:
                report.metrics.model_calls += 1
                report.metrics.agent_diagnoses += 1
                plan = self.diagnoser(
                    item=item, ir=ir, outputs=[outputs[c] for c in _COMPONENT_ORDER],
                    observation=observation,
                )
                event("component", "diagnose_failure", "passed", started=started,
                      details=[plan.rationale, *plan.repair_instructions])
            except Exception as exc:
                event("component", "diagnose_failure", "failed", started=started,
                      details=[f"{type(exc).__name__}: {exc}"])
                return False
        if plan is None or plan.action != "repair_components" or not plan.component_ids:
            return False
        if self.component_repairer is None:
            return False
        # A diagnosis Agent is not allowed to escape the component language.
        if any(component not in _COMPONENT_ORDER for component in plan.component_ids):
            return False
        changed = False
        for component_id in plan.component_ids:
            if repair_counts[component_id] >= self.policy.max_repairs_per_component:
                continue
            repair_counts[component_id] += 1
            started = time.monotonic()
            try:
                report.metrics.model_calls += 1
                repaired = self.component_repairer(
                    component_id=component_id, item=item, ir=ir,
                    current=outputs[component_id], review=plan,
                    dependency_outputs=_component_dependencies(ir, outputs, component_id),
                )
                if repaired.component_id != component_id:
                    raise ValueError(f"repair returned component_id={repaired.component_id}")
                outputs[component_id] = repaired
                self._save_component(root, repaired, report.contract_checksum, ir.ir_checksum or "")
                report.metrics.repaired_components += 1
                changed = True
                event("component", f"repair_round_{repair_counts[component_id]}", "repaired",
                      target=component_id, started=started, details=plan.repair_instructions)
            except Exception as exc:
                event("component", f"repair_round_{repair_counts[component_id]}", "failed",
                      target=component_id, started=started, details=[f"{type(exc).__name__}: {exc}"])
        return changed

    @staticmethod
    def _mark_manual(report: MaterializationReport) -> None:
        if not report.manual_intervention_required:
            report.manual_intervention_required = True
            report.metrics.manual_interventions += 1

    @staticmethod
    def _component_path(root: Path, component_id: str) -> Path:
        return root / "components" / f"{component_id}.json"

    def _save_component(self, root: Path, output: IRComponentOutput, contract_checksum: str, ir_checksum: str) -> None:
        path = self._component_path(root, output.component_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": "benchmark-forge.component-checkpoint.v1",
            "contract_checksum": contract_checksum,
            "ir_checksum": ir_checksum,
            "output": output.model_dump(mode="json"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_component(self, root: Path, component_id: str, contract_checksum: str, ir_checksum: str) -> IRComponentOutput | None:
        path = self._component_path(root, component_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("contract_checksum") != contract_checksum or payload.get("ir_checksum") != ir_checksum:
                return None
            output = IRComponentOutput.model_validate(payload["output"])
            return output if output.component_id == component_id else None
        except Exception:
            return None

    @staticmethod
    def _write_bundle(root: Path, bundle: EnvironmentScaffoldBundle) -> None:
        root.mkdir(parents=True, exist_ok=True)
        expected = {file.path for file in bundle.files}
        # Remove only stale files under this generated bundle root.
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file() and str(path.relative_to(root)) not in expected:
                path.unlink()
        for file in bundle.files:
            destination = root / file.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(file.content, encoding="utf-8")

    @staticmethod
    def _persist_report(root: Path, report: MaterializationReport) -> None:
        report_text = report.model_dump_json(indent=2)
        (root / "workflow-report.json").write_text(report_text, encoding="utf-8")
        events_text = "".join(event.model_dump_json() + "\n" for event in report.events)
        (root / "workflow-events.jsonl").write_text(events_text, encoding="utf-8")
        # Keep every replay as an immutable-enough run record while retaining
        # the stable top-level paths for simple consumers and resume tooling.
        history_root = root / "workflow-runs" / report.run_id
        history_root.mkdir(parents=True, exist_ok=True)
        (history_root / "report.json").write_text(report_text, encoding="utf-8")
        (history_root / "events.jsonl").write_text(events_text, encoding="utf-8")


class FixedContractReplayWorkflow:
    """Compile one existing Contract, then invoke the resumable component flow."""

    def __init__(self, *, compiler: Any, materializer: MaterializationWorkflow):
        self.compiler = compiler
        self.materializer = materializer

    def run(self, *, item: BenchmarkItem, output_root: str | Path) -> MaterializationReport:
        if item.executable_task is None:
            raise ValueError("fixed replay requires executable_task")
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        contract = item.executable_task
        contract_checksum = _checksum(contract)
        (root / "contract.json").write_text(contract.model_dump_json(indent=2), encoding="utf-8")
        ir_path = root / "environment-ir.json"
        checkpoint_path = root / "environment-ir-checkpoint.json"
        scenario_errors = validate_contract_realizability(item)
        prefix_events = [WorkflowEvent(
            sequence=1, stage="contract", action="validate_fixed_contract",
            status="failed" if scenario_errors else "passed",
            target=contract.task_id,
            details=[f"contract_checksum={contract_checksum}", *scenario_errors],
        )]
        if scenario_errors:
            report = MaterializationReport(
                status="scenario_incomplete", task_id=contract.task_id,
                environment_id=contract.environment.environment_id,
                contract_checksum=contract_checksum, manual_intervention_required=False,
                stop_reason="; ".join(scenario_errors), events=prefix_events,
            )
            MaterializationWorkflow._persist_report(root, report)
            return report
        compile_started = time.monotonic()
        try:
            ir: EnvironmentIR | None = None
            reused_ir = False
            if ir_path.exists() and checkpoint_path.exists():
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                cached = EnvironmentIR.model_validate_json(ir_path.read_text(encoding="utf-8"))
                if (
                    checkpoint.get("contract_checksum") == contract_checksum
                    and checkpoint.get("ir_checksum") == cached.ir_checksum
                    and cached.frozen and cached.ir_checksum == cached.semantic_checksum()
                    and cached.environment_id == contract.environment.environment_id
                ):
                    ir = cached
                    reused_ir = True
            if ir is None:
                ir = self.compiler.compile(contract)
                ir_path.write_text(ir.model_dump_json(indent=2), encoding="utf-8")
                checkpoint_path.write_text(json.dumps({
                    "schema_version": "benchmark-forge.ir-checkpoint.v1",
                    "contract_checksum": contract_checksum,
                    "ir_checksum": ir.ir_checksum,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            prefix_events.append(WorkflowEvent(
                sequence=2, stage="ir", action="compile_and_freeze",
                status="reused" if reused_ir else "passed",
                duration_seconds=round(time.monotonic() - compile_started, 3),
                details=[f"ir_checksum={ir.ir_checksum}"],
            ))
            _, report = self.materializer.run_components(item=item, ir=ir, output_root=root)
            report.metrics.reused_ir = int(reused_ir)
            compiler_attempts = 0 if reused_ir else max(1, int(getattr(self.compiler, "last_attempt_count", 1)))
            report.metrics.ir_compile_attempts = compiler_attempts
            report.metrics.model_calls += compiler_attempts
            report.events = prefix_events + report.events
            for sequence, event in enumerate(report.events, 1):
                event.sequence = sequence
            MaterializationWorkflow._persist_report(root, report)
            return report
        except IRExpressivenessError as exc:
            prefix_events.append(WorkflowEvent(
                sequence=2, stage="ir", action="compile_and_freeze", status="failed",
                duration_seconds=round(time.monotonic() - compile_started, 3), details=[str(exc)],
            ))
            report = MaterializationReport(
                status="requires_ir_extension", task_id=contract.task_id,
                environment_id=contract.environment.environment_id,
                contract_checksum=contract_checksum, manual_intervention_required=False,
                stop_reason=str(exc), metrics=MaterializationMetrics(
                    ir_compile_attempts=max(1, int(getattr(self.compiler, "last_attempt_count", 1))),
                    model_calls=max(1, int(getattr(self.compiler, "last_attempt_count", 1))),
                ),
                events=prefix_events,
            )
        except Exception as exc:
            prefix_events.append(WorkflowEvent(
                sequence=2, stage="ir", action="compile_and_freeze", status="failed",
                duration_seconds=round(time.monotonic() - compile_started, 3),
                details=[f"{type(exc).__name__}: {exc}"],
            ))
            report = MaterializationReport(
                status="failed", task_id=contract.task_id,
                environment_id=contract.environment.environment_id,
                contract_checksum=contract_checksum, manual_intervention_required=True,
                stop_reason=f"{type(exc).__name__}: {exc}",
                metrics=MaterializationMetrics(
                    manual_interventions=1,
                    ir_compile_attempts=max(1, int(getattr(self.compiler, "last_attempt_count", 1))),
                    model_calls=max(1, int(getattr(self.compiler, "last_attempt_count", 1))),
                ),
                events=prefix_events,
            )
        MaterializationWorkflow._persist_report(root, report)
        return report
