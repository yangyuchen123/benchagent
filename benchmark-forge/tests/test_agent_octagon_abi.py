from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from benchmark_forge import (
    ArtifactRequirement,
    BenchmarkItem,
    ContentReference,
    EnvironmentContract,
    ExecutableTaskContract,
    MaterialContract,
    ScoringContract,
    ScoringDimensionContract,
    ToolContract,
    WorkspaceContract,
)
from benchmark_forge.agent_octagon_abi import (
    validate_bundle_abi,
    validate_constraint_semantics,
    validate_scorer_abi,
)
from benchmark_forge.environment_ir import lower_contract_to_ir
from benchmark_forge.staging import EnvironmentScaffoldBundle, ScaffoldFile, normalize_octagon_scaffold, validate_scaffold


ENV_ID = "agent-octagon-abi-fixture-v1"
ARTIFACT_SCHEMA = {
    "type": "object",
    "required": ["result"],
    "properties": {"result": {"type": "string"}},
    "additionalProperties": False,
}


def _item() -> BenchmarkItem:
    contract = ExecutableTaskContract(
        task_id="agent-octagon-abi-task",
        instruction="Read materials/input.json, write the structured artifact, and call validate_output.",
        environment=EnvironmentContract(
            environment_id=ENV_ID,
            implementation=ContentReference(type="generated", ref="generated://abi-fixture/v1"),
            tools=[ToolContract(
                name="validate_output", ownership="benchmark_environment", interface="mcp",
                entrypoint={"input_schema": {"type": "object"}, "output_schema": {"type": "object"}},
            )],
            materials=[MaterialContract(
                material_id="input", source=ContentReference(type="generated", ref="generated://input/v1"),
                target="materials/input.json", visibility="agent",
            )],
            workspace=WorkspaceContract(writable_paths=["artifacts"]),
            maturity="generated_contract",
        ),
        artifacts=[ArtifactRequirement(
            path="artifacts/final.json", description="structured schema-valid result",
            media_type="application/json", schema_path="schemas/final.schema.json", schema_def=ARTIFACT_SCHEMA,
        )],
        scoring=ScoringContract(dimensions=[ScoringDimensionContract(
            name="completion", weight=100, description="artifact completion", evidence_sources=["artifact", "tool_trace"],
        )]),
    )
    return BenchmarkItem(
        item_id="abi-item", dimension_id="completion", source_mode="generated_environment",
        source_id="test", item_kind="executable_task", answer_type="artifact", executable_task=contract,
    )


CORE = '''from pathlib import Path
from octagon.env_api import EnvContext, env_tool

@env_tool(name="validate_output", description="validate output", parameters={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]})
def validate_output(ctx: EnvContext, path: str):
    workspace = ctx.trace.path.parent / "skill_workspace"
    return {"valid": (workspace / path).is_file()}
'''

MCP = '''import os
import httpx
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("abi-fixture")
ATTEMPT_ID = os.environ.get("OCTAGON_ATTEMPT_ID", "")
TOKEN = os.environ.get("OCTAGON_ENV_TOKEN", "")
BASE_URL = os.environ.get("OCTAGON_BASE_URL", "")
@mcp.tool()
def validate_output(path: str):
    return httpx.post(f"{BASE_URL}/attempts/{ATTEMPT_ID}/tools/validate_output", headers={"Authorization": f"Bearer {TOKEN}"}, json={"path": path}).json()
if __name__ == "__main__":
    mcp.run()
'''

SCORER = '''from pathlib import Path
def score(*, attempt_id, task, env_db=None, trace=None, final_state=None, **kwargs):
    workspace = Path(env_db).parent / "skill_workspace" if env_db else Path("missing")
    called = any(isinstance(row, dict) and row.get("tool_name") == "validate_output" for row in (trace or []))
    value = 100 if (workspace / "artifacts/final.json").is_file() and called else 0
    return [{"dimension":"completion", "value":value, "detail":"deterministic"}]
'''


def _normalized_bundle():
    item = _item()
    ir = lower_contract_to_ir(item.executable_task).freeze()
    bundle = EnvironmentScaffoldBundle(environment_id=ENV_ID, files=[
        ScaffoldFile(path="meta.yaml", content=f"name: {ENV_ID}\n"),
        ScaffoldFile(path="README.md", content="trusted ABI fixture"),
        ScaffoldFile(path="core.py", content=CORE),
        ScaffoldFile(path="mcp_server.py", content=MCP),
        ScaffoldFile(path="scorer.py", content=SCORER),
        ScaffoldFile(path="materials/input.json", content='{"input":"ok"}'),
        ScaffoldFile(path="schemas/input.schema.json", content='{"type":"object"}'),
        ScaffoldFile(path="schemas/final.schema.json", content=json.dumps(ARTIFACT_SCHEMA)),
        ScaffoldFile(path="tasks/task.json", content=json.dumps({
            "id": "agent-octagon-abi-task", "env_name": ENV_ID,
            "prompt": "Use materials/input.json and schemas/final.schema.json; write artifacts/final.json and call validate_output.",
            "timeout_seconds": 60,
        })),
        ScaffoldFile(path="tests/test_contract.py", content="def test_contract():\n    assert True\n"),
    ])
    return item, ir, normalize_octagon_scaffold(bundle, item, ir)


def test_normalizer_emits_current_agent_octagon_material_and_mcp_abi() -> None:
    item, ir, bundle = _normalized_bundle()
    files = {file.path: file.content for file in bundle.files}
    meta = yaml.safe_load(files["meta.yaml"])
    assert isinstance(meta["materials"], dict)
    mounts = {(entry["path"], entry["target"]) for entry in meta["materials"]["agent"]}
    assert {
        ("materials/input.json", "materials/input.json"),
        ("schemas/input.schema.json", "schemas/input.schema.json"),
        ("schemas/final.schema.json", "schemas/final.schema.json"),
    } <= mounts
    assert meta["entrypoints"]["mcp"]["enabled"] is True
    assert meta["entrypoints"]["mcp"]["command"] == ["python", "mcp_server.py"]
    assert meta["runtime_abi"] == "agent-octagon.env-loader.v1"
    validation = validate_scaffold(bundle, item, ir)
    assert validation.valid, validation.errors


def test_linter_rejects_legacy_list_materials_and_unregistered_tools() -> None:
    _, ir, bundle = _normalized_bundle()
    files = {file.path: file.content for file in bundle.files}
    meta = yaml.safe_load(files["meta.yaml"])
    meta["materials"] = ir.materials[0].model_dump(mode="json") if False else [ir.materials[0].model_dump(mode="json")]
    files["core.py"] = "def validate_output(arguments): return {'valid': True}\n"
    files["mcp_server.py"] = "def main(): pass\n"
    errors = validate_bundle_abi(meta=meta, files=files, ir=ir)
    assert any("materials must be an audience mapping" in error for error in errors)
    assert any("@env_tool registry mismatch" in error for error in errors)
    assert any("FastMCP registry mismatch" in error for error in errors)
    assert any("authenticated attempt routing" in error for error in errors)


def test_scorer_abi_rejects_none_values_and_optional_int_conversion() -> None:
    bad = '''def score(**kwargs):
    result = None
    value = int(result)
    return [{"dimension":"completion", "value":None, "detail":"bad"}]
'''
    errors = validate_scorer_abi(bad, {"completion"})
    assert any("value=None" in error for error in errors)
    assert any("optional result" in error for error in errors)
    assert validate_scorer_abi(SCORER, {"completion"}) == []


def test_constraint_semantics_linter_rejects_silent_undercoverage_and_missing_precedence() -> None:
    files = {
        "materials/input.json": json.dumps({
            "cases": [{
                "case_id": "conflict",
                "precedence": "higher priority wins",
                "constraints": [
                    {"priority": 10, "type": "must_include", "value": "approved"},
                    {"priority": 1, "type": "format", "value": "one sentence"},
                ],
            }]
        }),
        "scorer.py": "SUPPORTED_CONSTRAINT_TYPES = frozenset({'must_include'})\n",
    }
    errors = validate_constraint_semantics(files)
    assert any("lack evaluators" in error and "format" in error for error in errors)
    assert any("fail closed" in error for error in errors)
    assert any("_effective_constraints" in error for error in errors)


def test_constraint_semantics_linter_accepts_explicit_complete_protocol() -> None:
    files = {
        "materials/input.json": json.dumps({
            "cases": [{
                "case_id": "conflict",
                "precedence": "higher priority wins",
                "constraints": [
                    {"priority": 10, "type": "must_include", "value": "approved"},
                    {"priority": 1, "type": "format", "value": "one sentence"},
                ],
            }]
        }),
        "scorer.py": '''SUPPORTED_CONSTRAINT_TYPES = frozenset({"must_include", "format"})
def _effective_constraints(case): return case["constraints"]
def evaluate(constraint):
    raise ValueError(f"unsupported constraint type: {constraint['type']}")
''',
    }
    assert validate_constraint_semantics(files) == []


def test_real_agent_octagon_loader_accepts_normalized_trusted_fixture(tmp_path: Path) -> None:
    '''Cross-project ABI test using trusted fixture code, never LLM-generated code.'''
    octagon_root = Path('/home/yang/agent-octagon')
    python = octagon_root / '.venv/bin/python'
    if not python.is_file():
        pytest.skip('AgentOctagon runtime unavailable')
    _, _, bundle = _normalized_bundle()
    env_root = tmp_path / 'envs' / ENV_ID
    for file in bundle.files:
        destination = env_root / file.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(file.content, encoding='utf-8')

    script = r'''import json, sqlite3, sys
from pathlib import Path
from backend.env_loader import EnvLoader
from backend.run_dispatch import _copy_agent_materials, _material_entries, _mcp_server_specs
from octagon.env_api import EnvContext, TraceWriter

envs_path, data_raw, env_id = sys.argv[1:4]
data_path = Path(data_raw)
loaded = EnvLoader(Path(envs_path)).load_all()[env_id]
entries = _material_entries(loaded, "agent")
attempt_id = "att_abi_fixture"
context = {}
_copy_agent_materials(data_path, attempt_id, loaded, context)
workspace = data_path / "attempts" / attempt_id / "skill_workspace"
artifact = workspace / "artifacts/final.json"
artifact.parent.mkdir(parents=True)
artifact.write_text('{"result":"ok"}', encoding="utf-8")
ctx = EnvContext(
    attempt_id=attempt_id, env_session_id="session-abi", db=sqlite3.connect(":memory:"),
    trace=TraceWriter(data_path=data_path, attempt_id=attempt_id, env_session_id="session-abi"),
)
result = loaded.tools["validate_output"].call(ctx, path="artifacts/final.json")
mcp_specs = _mcp_server_specs(loaded)
print(json.dumps({
    "tools": sorted(loaded.tools),
    "mcp_count": len(mcp_specs),
    "mcp_cwd": mcp_specs[0].cwd if mcp_specs else None,
    "mcp_script": str(Path(mcp_specs[0].cwd) / mcp_specs[0].args[-1]) if mcp_specs else None,
    "mcp_script_exists": (Path(mcp_specs[0].cwd) / mcp_specs[0].args[-1]).is_file() if mcp_specs else False,
    "targets": sorted(entry["target"] for entry in entries),
    "material_exists": (workspace / "materials/input.json").is_file(),
    "schema_exists": (workspace / "schemas/final.schema.json").is_file(),
    "result": result,
}))
'''
    completed = subprocess.run(
        [str(python), '-c', script, str(tmp_path / 'envs'), str(tmp_path / 'data'), ENV_ID],
        cwd=octagon_root, text=True, capture_output=True, check=True, timeout=30,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result['tools'] == ['validate_output']
    assert result['mcp_count'] == 1
    assert result['mcp_cwd'] == str(env_root.resolve())
    assert result['mcp_script'] == str((env_root / 'mcp_server.py').resolve())
    assert result['mcp_script_exists'] is True
    assert set(result['targets']) >= {
        'materials/input.json', 'schemas/input.schema.json', 'schemas/final.schema.json',
    }
    assert result['material_exists'] is True
    assert result['schema_exists'] is True
    assert result['result'] == {'valid': True}
