from __future__ import annotations

"""Agent role that lowers a semantic Benchmark Contract into Environment IR."""

from dataclasses import dataclass, field
from typing import Any

from .environment_ir import (EnvironmentIR, EnvironmentIRDraft, IRExpressivenessError,
                             analyze_contract_expressiveness, normalize_ir_draft,
                             validate_ir_contract_bindings)
from .pydantic_ai_adapter import PydanticAIRunner
from .domain import ExecutableTaskContract


class IRCompilationError(RuntimeError):
    """The compiler role could not produce a valid frozen IR after rewrites."""


@dataclass
class EnvironmentIRCompilerAgent:
    """Compiler role: semantic completion by Agent, validation by Forge.

    The role is intentionally separate from Design and Executor. It does not
    create code or files. It resolves the implementation bindings that
    component agents must share: tools, states, artifacts, evidence, rubric
    references, and task bindings.
    """

    model: Any
    knowledge_base: Any | None = None
    max_rewrites: int = 2
    timeout: float = 90.0
    retries: int = 0
    last_attempt_count: int = field(default=0, init=False)

    instructions: str = (
        "You are the Environment IR Compiler Agent. Lower the supplied "
        "ExecutableTaskContract into an EnvironmentIRDraft. Do not write Python, "
        "YAML, scorer code, or explanations. Do not redesign the benchmark goal. "
        "Resolve canonical IDs and cross-component bindings before code generation. "
        "Every tool must have a stable canonical tool_id matching ^[a-z][a-z0-9_-]*$; "
        "never use interface prefixes, dots, slashes, spaces, or object paths in IDs "
        "(for example use read_material, not tool.read_material). The same identifier "
        "rule applies to state_id, artifact_id, evidence_id, criterion_id, and node_id. "
        "Every tool must have input/output schema. Every "
        "artifact must have one canonical artifact_id and path. When the Contract calls a JSON artifact "
        "structured, schema-valid, or exposes a validator for it, treat its missing concrete schema as a fillable "
        "hole: define one constraining public schema_def, bind a canonical schemas/<artifact_id>.schema.json "
        "schema_path, and include required_feature artifact_schema. A bare {type: object}, minProperties-only, "
        "or arbitrary additionalProperties object is not a schema contract. If scoring requires coverage of every "
        "scenario case, the schema must contain a required per-case array with minItems at least IRScenario.minimum_items; "
        "its object items must require an identity field and a response field. If the Contract already supplies schema_def/schema_path, preserve them exactly. Preserve every typed MaterialContract as one "
        "IRMaterial with the same material_id, source binding, target, visibility and minimum-items policy. Preserve "
        "EnvironmentContract.scenario as IRScenario without weakening minimum_items, allow_empty, case tags, or its "
        "generator/injection bindings. Every runtime state "
        "must name its producer and consumers. State producer/consumer references "
        "may only use registered tool_id, coordination node_id, rubric criterion_id, "
        "or reserved subjects exactly runtime, scorer, tests, agent. The literal values "
        "task_binding, manifest, task, verifier, evaluator, and descriptive phase names are not "
        "valid state consumers. Task use is represented by task_binding.state_refs, not by adding "
        "task_binding to IRStateField.consumers. Never emit a free-form label. "
        "For rubric consumers, copy the exact declared rubric.criteria criterion_id; "
        "if no exact criterion exists, omit that consumer instead of inventing a semantic name. "
        "Every evidence source must declare "
        "authority and read_interface. Every rubric criterion must reference only "
        "declared evidence/artifacts/states. Task bindings must reference the same "
        "canonical IDs. Include manifest, runtime, scorer, and tests components. "
        "Component owned_paths are fixed filesystem protocol values: manifest="
        "[meta.yaml, README.md, tasks/], runtime=[core.py, mcp_server.py], "
        "scorer=[scorer.py, scorer_fixtures/], tests=[tests/]. Never put runtime "
        "state IDs, rubric IDs, artifact paths, or semantic labels in owned_paths. "
        "Never invent private answers, hidden files, credentials, or unavailable "
        "services. required_features must be exactly the set of actually present typed constructs: "
        "use the core features by default; include coordination_graph if and only if "
        "the Contract has a CoordinationContract and the draft contains a typed coordination object. "
        "Fault injection, retries, resource limits, or observable tool traces do not imply coordination_graph. "
        "Classify every tool ownership explicitly. benchmark_environment means a deterministic domain/control "
        "operation implemented by the generated bundle. agent_runtime means an existing host-Agent capability "
        "such as subagent_spawn/subagent_message/subagent_wait/subagent_trace or native workspace operations; "
        "the generated runtime must not simulate it. evaluation_system means hidden observation/verifier logic "
        "that is not callable by the tested Agent. If the target capability is native subagent delegation, bind "
        "those operations as agent_runtime and consume host trajectory evidence rather than inventing synthetic children. "
        "The only allowed required_features are exactly: "
        "tool_registry, runtime_state, artifact_registry, evidence_authority, "
        "rubric_binding, task_binding, workspace_policy, coordination_graph, material_registry, scenario_model, artifact_schema. "
        "Do not emit feature names for individual coordination properties such as "
        "parallel_independent_subtasks, repair_logging, or public_validation; "
        "those are fields inside coordination_graph. Return only EnvironmentIRDraft."
    )

    def compile(self, contract: ExecutableTaskContract) -> EnvironmentIR:
        self.last_attempt_count = 0
        analyze_contract_expressiveness(contract)
        retrieved = None
        if self.knowledge_base is not None:
            retrieved = self.knowledge_base.context(
                f"{contract.task_id} {contract.instruction}",
                role="ir_compiler",
                source_kinds=["environment_profile", "task_spec", "documentation"],
                limit=6,
                max_chars=8_000,
            )
        prompt = (
            f"ExecutableTaskContract:\n{contract.model_dump_json(indent=2)}\n\n"
            f"Retrieved public implementation precedents:\n{retrieved!r}"
        )
        errors: list[str] = []
        for attempt in range(self.max_rewrites + 1):
            self.last_attempt_count = attempt + 1
            instructions = self.instructions
            if errors:
                instructions += (
                    "\nThe previous draft was rejected by deterministic IR validation. "
                    "Rewrite the complete draft and fix every reported error; do not "
                    "explain the fixes. Errors:\n" + "\n".join(errors)
                )
            runner = PydanticAIRunner(
                model=self.model,
                output_type=EnvironmentIRDraft,
                instructions=instructions,
                timeout=self.timeout,
                retries=self.retries,
                label=f"ir_compiler.attempt_{attempt + 1}",
            )
            try:
                draft = runner.run_sync(prompt)
                ir = normalize_ir_draft(draft)
                validate_ir_contract_bindings(contract, ir)
                return ir
            except IRExpressivenessError as exc:
                if exc.contract_gap:
                    # This is a language gap, not a malformed draft. Rewriting
                    # in the same language would only encourage degradation.
                    raise
                errors = [f"attempt {attempt + 1}: {type(exc).__name__}: {exc}"]
            except Exception as exc:
                errors = [f"attempt {attempt + 1}: {type(exc).__name__}: {exc}"]
        raise IRCompilationError(
            f"IR compiler exhausted {self.max_rewrites + 1} attempts: " + "; ".join(errors)
        )
