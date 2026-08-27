from __future__ import annotations

"""Component code-generation agents driven by a frozen Environment IR.

Each role emits only an ``IRComponentOutput`` for its owned paths.  The Forge
linker, not the model, decides whether those outputs form a bundle.
"""

from dataclasses import dataclass
import hashlib
from typing import Any, Literal

from .environment_ir import EnvironmentIR, IRComponentOutput
from .pydantic_ai_adapter import PydanticAIRunner
from .staging import EnvironmentScaffoldBundle
from .domain import BenchmarkItem
from .scorer_design import ScorerDesign


ComponentId = Literal["manifest", "runtime", "scorer", "tests"]


@dataclass
class ComponentAgentFailure(RuntimeError):
    component_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.component_id} component agent failed: {self.message}"


def _dump(value: Any) -> str:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json(indent=2)
    if hasattr(value, "model_dump"):
        import json
        return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return str(value)


_COMPONENT_INSTRUCTIONS: dict[str, str] = {
    "manifest": """
You are the Manifest Component Agent. Implement ONLY the manifest component from
Frozen EnvironmentIR. Return exactly one IRComponentOutput with component_id
manifest. You may write meta.yaml, README.md, tasks/*.json, materials/*,
and schemas/* only. Material files must implement every generated, required,
Agent-visible IRMaterial binding and satisfy the typed IRScenario minimum item
and case-tag constraints. For every IRArtifact using artifact_schema, write the exact
IRArtifact.schema_def to its canonical schema_path. Expose that schema path and the
artifact's required top-level fields in README.md and the public task prompt; never
substitute a fixture schema for the final artifact schema. Do not write Python, scorer code, tests, hidden
answers, or arbitrary files. Use the
canonical environment_id, task_id, Agent-callable tool IDs, artifact IDs,
evidence IDs and rubric criteria from the IR. Do not expose tools owned by
evaluation_system in public task or meta tool listings. The task JSON must be publicly executable and
contain id, env_name, prompt, timeout_seconds. Target the current AgentOctagon
env-loader ABI: meta.yaml materials is an audience mapping with materials.agent
entries containing local path and workspace target; declare every public material
and every schemas/* file. Put typed IR details in material_contracts, not in the
loader-owned materials field. When benchmark_environment tools exist, declare one
entrypoints.mcp object with enabled=true, stdio transport, and a relocatable command
launching mcp_server.py from the environment directory. Never assume the bundle is
installed under envs/<environment_id>. Environment directory names use
lowercase ASCII hyphen-safe IDs. Return only the typed output.
""",
    "runtime": """
You are the Runtime Component Agent. Implement ONLY runtime from Frozen
EnvironmentIR. Return exactly one IRComponentOutput with component_id runtime.
You may write core.py, mcp_server.py, and only those paths. Target the current
AgentOctagon ABI exactly. In core.py import EnvContext and env_tool from
octagon.env_api and register every benchmark_environment tool with
@env_tool(name=<canonical tool_id>, description=..., parameters=<input schema>).
Tool functions receive ctx: EnvContext and must resolve the attempt workspace from
ctx.trace.path.parent / "skill_workspace"; do not rely on OCTAGON_WORKSPACE. In
mcp_server.py use FastMCP, expose matching @mcp.tool() functions, and proxy each
call to {OCTAGON_BASE_URL}/attempts/{OCTAGON_ATTEMPT_ID}/tools/<tool_id> with
Bearer OCTAGON_ENV_TOKEN. Never implement an unauthenticated direct endpoint or a
private JSON-lines protocol. Implement the registered benchmark_environment tool schemas, protocol entrypoints,
observable runtime state and workspace behavior. Consume material paths,
generator bindings, and injection bindings exactly as declared by IRMaterial /
IRScenario. A required scenario input that is unavailable is an invalid
environment configuration: fail closed with an explicit error/status; never
silently convert it to an empty collection unless IRScenario.allow_empty=true.
Never implement or simulate an IR tool whose ownership is agent_runtime; those calls are supplied by the
host Agent and observed through run traces. Never expose evaluation_system
verifiers/oracles as callable Agent tools. Validate Agent artifacts against the exact
IRArtifact.schema_def/schema_path; do not guess a schema from arbitrary fixture keys,
and do not treat a JSON object alone as schema-valid. Do not rename tools/artifacts/states,
invent hidden data, or write scorer/tests/manifest files. core.py may be a documented minimal
module when native agent/subagent behavior is evaluated, but the protocol must
remain coherent. Return only the typed output.
""",
    "scorer": """
You are the Scorer Component Agent. Implement ONLY scorer from Frozen
EnvironmentIR. Return exactly one IRComponentOutput with component_id scorer.
You may write scorer.py and scorer_fixtures/*. Use only canonical artifact,
evidence, state and rubric references in the IR. Parse artifact records according to
the exact IRArtifact.schema_def rather than accepting a list of guessed field aliases. scorer.py must expose
score(*, attempt_id, task, env_db=None, trace=None, final_state=None, **kwargs)
and return dimension/value/detail records. Current AgentOctagon calls scorer with
env_db as a pathlib.Path to attempts/<attempt_id>/env.db, trace as a list of tool-call
rows, and final_state as a dict. Resolve the Agent workspace as
Path(env_db).parent / "skill_workspace" and read public materials/artifacts there;
do not treat env_db as a material/artifact registry mapping. Read validator evidence
from trace rows whose tool_name equals the canonical tool id and whose result is the
returned object; do not expect trace={events:[...]}. Evidence authority and fallbacks
must be explicit and based on publicly observable data; never require hidden
answers or self-report alone. For a data-dependent IRScenario, distinguish
Agent performance from benchmark configuration failure. Missing material,
insufficient item count, missing required case tags, or unavailable required
injection/generator evidence must return every declared rubric dimension with a
numeric value of 0 plus status=invalid_environment and concrete detail; never emit
value=None because current AgentOctagon aggregates every row numerically. Agent
failure paths (missing/unparseable artifact, validator not called, timeout or partial
trace) must also return deterministic numeric zero/partial rows rather than raise.
Only the detail/status distinguishes infrastructure invalidity from Agent failure
until the peer runtime consumes that status. Never call int() or float() on an
optional value without an explicit None/type guard. An empty scenario must never earn
credit unless IRScenario.allow_empty=true. Coordination node IDs and the exact IR DAG are
implementation references, not a hidden gold decomposition: do not require exact
node IDs, an exact node count, or one exact dependency graph unless those values
are explicitly named in the public task prompt. Score equivalent decompositions
by rubric properties such as coverage, valid dependency direction, parallelism,
write-scope isolation, acceptance and integration. When public materials contain
typed constraints, declare a literal SUPPORTED_CONSTRAINT_TYPES set containing every
material constraint type and implement each type. Unknown types are
invalid_environment; never return None or silently skip them. If any case declares
precedence, lower constraints through a dedicated _effective_constraints function
before evaluation so overridden lower-priority conflicts cannot be scored. Keep
unjustified_deviation independent from ordinary constraint failures, and make
intent_coverage inspect substantive response intent rather than only case IDs. Do not
write runtime/manifest/tests files.
Return only the typed output.
""",
    "tests": """
You are the Test Component Agent. Implement ONLY tests from Frozen
EnvironmentIR. Return exactly one IRComponentOutput with component_id tests.
You may write tests/*. Add deterministic contract, protocol, artifact and
scorer smoke tests using public fixtures. Assert the canonical artifact schema file
equals IRArtifact.schema_def, the public task names its schema_path, runtime rejects a
structurally invalid artifact, and scorer calibration payloads conform to that same schema. Refer to canonical IDs and paths
from the IR; do not invent names. Require only benchmark_environment tools
from the generated MCP/runtime registry. Agent-runtime capabilities must be
validated from host traces or declared capability bindings, never from a fake
generated implementation. Tests run in a clean environment before any
agent attempt. Validate production material bindings, scenario minimum counts,
required case tags, Runtime fail-closed behavior, and scorer
``invalid_environment`` handling with numeric values for every dimension. Cover
missing artifact, malformed JSON, validator not called, absent validation state,
timeout/partial trace and missing required case paths; score() must never raise. Add calibration cases proving strong > partial
> unsafe execution when a data-dependent scenario is present. For every public
constraint type, include at least one positive and one negative calibration. Include
a precedence regression where a correct high-priority answer passes and the
overridden low-priority claim fails. Calibration "strong" responses must actually
satisfy the public fixture; placeholders such as "ok" are forbidden. Assert
unsupported constraint types return invalid_environment rather than receiving partial
credit. Never assert that runtime-produced artifacts already exist;
create temporary fixtures or test schema/functions without requiring a prior
attempt. The flat bundle owns mcp_server.py at its root: import mcp_server or
invoke ``python -m mcp_server``; never invent a package module. Validate public
behavior by importing modules and calling their public functions or entrypoints.
Never read implementation files or use ``inspect.getsource`` to assert that a
protocol ID, tool ID, artifact path, scorer reference, or other literal appears
in source code; multiple implementations may satisfy the same public contract.
The canonical scorer interface is public and fixed: call
``score(attempt_id=..., task=..., env_db=None, trace=None, final_state=None)``
with a task mapping and temporary artifact fixtures. The canonical result is a
list of dimension/value/detail records; do not require a Mapping result. An MCP
runtime may expose tools through one generic public protocol adapter such as
``handle``/``dispatch`` plus ``tools/list`` and ``tools/call``; do not require a
separate Python function for every tool. Do not guess positional,
``workspace``, or ``artifacts_dir`` scorer signatures. Do not write implementation
files or hidden answers. Return only the typed output.
""",
}


def _dependency_view(outputs: list[IRComponentOutput]) -> list[IRComponentOutput]:
    """Avoid copying large material bodies into every downstream Agent prompt."""
    viewed: list[IRComponentOutput] = []
    for output in outputs:
        files = []
        for file in output.files:
            content = file.content
            if file.path.startswith("materials/") and len(content.encode("utf-8")) > 2_000:
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                content = (
                    f"[material body omitted from dependency prompt; bytes={len(file.content.encode('utf-8'))}; "
                    f"sha256={digest}; consume through the declared public material/runtime interface]"
                )
            files.append(file.model_copy(update={"content": content}))
        viewed.append(output.model_copy(update={"files": files}))
    return viewed


def _prompt(component_id: str, item: BenchmarkItem, ir: EnvironmentIR, scorer_design: ScorerDesign | None,
            dependency_outputs: list[IRComponentOutput] | None = None) -> str:
    dependency_context = _dump(_dependency_view(dependency_outputs or []))
    return (
        f"Frozen EnvironmentIR (checksum={ir.ir_checksum or ir.semantic_checksum()}):\n"
        f"{_dump(ir)}\n\n"
        f"Original executable contract/item:\n{_dump(item)}\n\n"
        f"Verification & Control scorer design (optional):\n{_dump(scorer_design)}\n\n"
        f"Already generated dependency components (public implementation interface; do not test source text):\n{dependency_context}\n\n"
        f"Generate component={component_id}. The IR is authoritative; do not lower or reinterpret it."
    )


def generate_component_output(*, model: Any, component_id: ComponentId, item: BenchmarkItem,
                              ir: EnvironmentIR, scorer_design: ScorerDesign | None = None,
                              dependency_outputs: list[IRComponentOutput] | None = None,
                              timeout: float = 90.0, retries: int = 0) -> IRComponentOutput:
    if not ir.frozen:
        raise ComponentAgentFailure(component_id, "component codegen requires frozen IR")
    runner = PydanticAIRunner(
        model=model,
        output_type=IRComponentOutput,
        instructions=_COMPONENT_INSTRUCTIONS[component_id],
        timeout=timeout,
        retries=retries,
        label=f"component.{component_id}.generate",
    )
    try:
        output = runner.run_sync(_prompt(component_id, item, ir, scorer_design, dependency_outputs))
    except Exception as exc:
        raise ComponentAgentFailure(component_id, str(exc)) from exc
    if output.component_id != component_id:
        raise ComponentAgentFailure(component_id, f"returned component_id={output.component_id}")
    return output


def repair_component_output(*, model: Any, component_id: ComponentId, item: BenchmarkItem,
                            ir: EnvironmentIR, current: IRComponentOutput,
                            review: Any, scorer_design: ScorerDesign | None = None,
                            dependency_outputs: list[IRComponentOutput] | None = None,
                            timeout: float = 90.0, retries: int = 0) -> IRComponentOutput:
    """Repair exactly one component while preserving all other linked files."""
    if not ir.frozen:
        raise ComponentAgentFailure(component_id, "component repair requires frozen IR")
    instructions = _COMPONENT_INSTRUCTIONS[component_id] + """

This is a bounded repair. Preserve valid files and public behavior unless the
review identifies a concrete defect. Return only the repaired component output;
do not return a complete environment bundle and do not touch another component.
"""
    prompt = (
        f"{_prompt(component_id, item, ir, scorer_design, dependency_outputs)}\n\n"
        f"Current component output:\n{_dump(current)}\n\n"
        f"Deterministic/semantic review to address:\n{_dump(review)}"
    )
    runner = PydanticAIRunner(
        model=model, output_type=IRComponentOutput, instructions=instructions,
        timeout=timeout, retries=retries,
        label=f"component.{component_id}.repair",
    )
    try:
        output = runner.run_sync(prompt)
    except Exception as exc:
        raise ComponentAgentFailure(component_id, str(exc)) from exc
    if output.component_id != component_id:
        raise ComponentAgentFailure(component_id, f"returned component_id={output.component_id}")
    return output


def generate_component_outputs(*, model: Any, item: BenchmarkItem, ir: EnvironmentIR,
                               scorer_design: ScorerDesign | None = None,
                               timeout: float = 90.0, retries: int = 0) -> list[IRComponentOutput]:
    """Generate in dependency order so each component sees the same IR.

    Calls are deliberately sequential in the MVP: this keeps logs/retries
    deterministic and lets later agents rely on manifest/runtime contracts.
    Parallel execution can be added after the linker has explicit dependency
    scheduling.
    """
    generated: list[IRComponentOutput] = []
    specs = {component.component_id: component for component in ir.components}
    for component_id in ("manifest", "runtime", "scorer", "tests"):
        dependency_ids = set(specs[component_id].depends_on)
        dependencies = [output for output in generated if output.component_id in dependency_ids]
        generated.append(generate_component_output(
            model=model, component_id=component_id, item=item, ir=ir,
            scorer_design=scorer_design, dependency_outputs=dependencies,
            timeout=timeout, retries=retries,
        ))
    return generated
