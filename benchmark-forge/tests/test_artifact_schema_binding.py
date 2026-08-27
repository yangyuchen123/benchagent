from __future__ import annotations

import json
from pathlib import Path

from benchmark_forge import (
    ArtifactRequirement,
    BenchmarkItem,
    ContentReference,
    EnvironmentContract,
    ExecutableTaskContract,
    ScoringContract,
    ScoringDimensionContract,
    ToolContract,
    WorkspaceContract,
)
from benchmark_forge.environment_ir import (
    lower_contract_to_ir,
    validate_ir_contract_bindings,
)
from benchmark_forge.staging import (
    EnvironmentScaffoldBundle,
    ScaffoldFile,
    normalize_octagon_scaffold,
    validate_scaffold,
)


OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["cases"],
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["case_id", "response"],
                "properties": {
                    "case_id": {"type": "string"},
                    "response": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}


def _contract(*, explicit_schema: bool) -> ExecutableTaskContract:
    artifact = ArtifactRequirement(
        path="artifacts/final_output.json",
        description="One schema-valid structured JSON deliverable.",
        media_type="application/json",
        schema_path="schemas/final_output.schema.json" if explicit_schema else None,
        schema_def=OUTPUT_SCHEMA if explicit_schema else {},
    )
    return ExecutableTaskContract(
        task_id="artifact-schema-task",
        instruction=(
            "Produce the structured final artifact and call validate_output. "
            "The artifact must follow its public JSON schema."
        ),
        environment=EnvironmentContract(
            environment_id="artifact-schema-environment-v1",
            implementation=ContentReference(type="generated", ref="generated://artifact-schema/v1"),
            tools=[ToolContract(
                name="validate_output",
                ownership="benchmark_environment",
                interface="builtin",
                entrypoint={
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                },
            )],
            entrypoints={},
            workspace=WorkspaceContract(writable_paths=["artifacts"]),
            maturity="generated_contract",
        ),
        artifacts=[artifact],
        scoring=ScoringContract(
            dimensions=[ScoringDimensionContract(
                name="completion",
                weight=100,
                description="The public structured artifact is correct.",
                evidence_sources=["artifact", "tool_trace"],
            )],
            pass_threshold=60,
        ),
    )


def _item(contract: ExecutableTaskContract) -> BenchmarkItem:
    return BenchmarkItem(
        item_id="artifact-schema-item",
        dimension_id="instruction_following",
        source_mode="generated_environment",
        source_id="test",
        item_kind="executable_task",
        answer_type="artifact",
        executable_task=contract,
    )


def _bundle(item: BenchmarkItem, *, schema_source: str | None = None, mention_schema: bool = True):
    prompt = "Write artifacts/final_output.json and call validate_output."
    if mention_schema:
        prompt += " Follow schemas/final_output.schema.json."
    files = [
        ScaffoldFile(path="meta.yaml", content="name: artifact-schema-environment-v1\n"),
        ScaffoldFile(path="README.md", content="Typed artifact benchmark."),
        ScaffoldFile(path="tasks/artifact-schema-task.json", content=json.dumps({
            "id": "artifact-schema-task",
            "env_name": "artifact-schema-environment-v1",
            "prompt": prompt,
            "timeout_seconds": 60,
        })),
        ScaffoldFile(path="core.py", content=(
            "from octagon.env_api import EnvContext, env_tool\n"
            "@env_tool(name='validate_output', description='validate', parameters={'type':'object','properties':{}})\n"
            "def validate_output(ctx: EnvContext):\n    return {'valid': True}\n"
        )),
        ScaffoldFile(path="mcp_server.py", content=(
            "import os, httpx\nfrom mcp.server.fastmcp import FastMCP\n"
            "mcp=FastMCP('test')\n"
            "ATTEMPT_ID=os.environ.get('OCTAGON_ATTEMPT_ID','')\n"
            "TOKEN=os.environ.get('OCTAGON_ENV_TOKEN','')\n"
            "BASE=os.environ.get('OCTAGON_BASE_URL','')\n"
            "@mcp.tool()\ndef validate_output():\n"
            "    return httpx.post(f'{BASE}/attempts/{ATTEMPT_ID}/tools/validate_output', headers={'Authorization':f'Bearer {TOKEN}'}, json={}).json()\n"
        )),
        ScaffoldFile(
            path="scorer.py",
            content=(
                "from pathlib import Path\n"
                "def score(*, attempt_id, task, env_db=None, trace=None, final_state=None, **kwargs):\n"
                "    workspace = Path(env_db).parent / 'skill_workspace' if env_db else Path('missing')\n"
                "    called = any(isinstance(row, dict) and row.get('tool_name') == 'validate_output' for row in (trace or []))\n"
                "    return [{'dimension':'completion','value':0,'detail':f'called={called}; workspace={workspace}'}]\n"
            ),
        ),
        ScaffoldFile(path="tests/test_contract.py", content="def test_contract():\n    assert True\n"),
    ]
    if schema_source is not None:
        files.append(ScaffoldFile(path="schemas/final_output.schema.json", content=schema_source))
    return EnvironmentScaffoldBundle(environment_id="artifact-schema-environment-v1", files=files)


def test_schema_claim_is_a_fillable_ir_hole_not_a_generic_object() -> None:
    contract = _contract(explicit_schema=False)
    generic_ir = lower_contract_to_ir(contract)
    try:
        validate_ir_contract_bindings(contract, generic_ir)
    except ValueError as exc:
        assert "schema_def is unconstrained" in str(exc)
    else:
        raise AssertionError("schema-valid artifact must not compile to a generic object")


def test_explicit_artifact_schema_lowers_to_versioned_typed_ir() -> None:
    contract = _contract(explicit_schema=True)
    ir = lower_contract_to_ir(contract).freeze()
    validate_ir_contract_bindings(contract, ir)
    assert ir.ir_version == "1.3"
    assert "artifact_schema" in ir.required_features
    assert ir.artifacts[0].schema_path == "schemas/final_output.schema.json"
    assert ir.artifacts[0].schema_def == OUTPUT_SCHEMA


def test_scaffold_requires_one_canonical_schema_shared_with_public_task() -> None:
    item = _item(_contract(explicit_schema=True))
    ir = lower_contract_to_ir(item.executable_task).freeze()

    missing = normalize_octagon_scaffold(_bundle(item, schema_source=None), item, ir)
    result = validate_scaffold(missing, item, ir)
    assert any("canonical artifact schema missing" in error for error in result.errors)

    mismatched = normalize_octagon_scaffold(
        _bundle(item, schema_source=json.dumps({"type": "object"})), item, ir,
    )
    result = validate_scaffold(mismatched, item, ir)
    assert any("does not equal Frozen IR schema_def" in error for error in result.errors)

    hidden = normalize_octagon_scaffold(
        _bundle(item, schema_source=json.dumps(OUTPUT_SCHEMA), mention_schema=False), item, ir,
    )
    result = validate_scaffold(hidden, item, ir)
    assert any("public task prompt does not name artifact schema_path" in error for error in result.errors)

    valid = normalize_octagon_scaffold(
        _bundle(item, schema_source=json.dumps(OUTPUT_SCHEMA), mention_schema=True), item, ir,
    )
    result = validate_scaffold(valid, item, ir)
    assert result.valid, result.errors


def test_previous_real_contract_now_rejects_unconstrained_artifact_ir() -> None:
    contract_path = Path("run/material-scenario-materialized-20260827/contract.json")
    if not contract_path.exists():
        return
    contract = ExecutableTaskContract.model_validate_json(contract_path.read_text(encoding="utf-8"))
    ir = lower_contract_to_ir(contract)
    try:
        validate_ir_contract_bindings(contract, ir)
    except ValueError as exc:
        assert "schema_def is unconstrained" in str(exc)
    else:
        raise AssertionError("the recorded regression contract must require artifact-schema hole filling")
