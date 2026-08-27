from __future__ import annotations

"""Current AgentOctagon environment ABI adapter and static linter.

Benchmark Forge owns its typed Contract/IR.  AgentOctagon owns the executable
loader ABI.  This module is the narrow product boundary between them; it does
not import or call AgentOctagon at generation time.
"""

import ast
import json
from pathlib import Path
from typing import Any

from .environment_ir import EnvironmentIR


AGENT_OCTAGON_ABI_VERSION = "agent-octagon.env-loader.v1"


def material_mounts(ir: EnvironmentIR, file_paths: set[str]) -> dict[str, list[dict[str, str]]]:
    """Translate typed IR resources to AgentOctagon's audience/path mount ABI."""
    entries: list[dict[str, str]] = []
    for material in ir.materials:
        if material.visibility == "agent":
            entries.append({"path": material.target, "target": material.target})
    for artifact in ir.artifacts:
        if artifact.schema_path:
            entries.append({"path": artifact.schema_path, "target": artifact.schema_path})
    # Manifest-generated schemas are public protocol files unless placed under
    # private/.  Copy all of them so a public task can safely reference one.
    for path in sorted(file_paths):
        if path.startswith("schemas/") and not path.endswith("/"):
            entries.append({"path": path, "target": path})
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry["path"], entry["target"])
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return {"agent": deduped}


def mcp_entrypoint(environment_id: str) -> dict[str, Any]:
    """Return the relocatable AgentOctagon stdio MCP declaration.

    The command is intentionally relative to the environment directory.  The
    peer runtime owns resolving ``cwd`` to ``env.env_dir``; Forge must not guess
    that a generated bundle will later be installed under ``envs/<name>``.
    """
    return {
        "mcp": {
            "enabled": True,
            "transport": "stdio",
            "name": f"octagon-{environment_id}",
            # ``python`` is an ABI token resolved by AgentOctagon to its own
            # runtime interpreter, which owns the MCP dependencies.
            "command": ["python", "mcp_server.py"],
        }
    }


def _literal_string_set(tree: ast.AST, name: str) -> set[str] | None:
    """Read a top-level literal set/frozenset/tuple/list assignment."""
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in {"set", "frozenset"}:
            value = value.args[0] if value.args else None
        if not isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            return None
        values: set[str] = set()
        for element in value.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                return None
            values.add(element.value)
        return values
    return None


def validate_constraint_semantics(files: dict[str, str]) -> list[str]:
    """Reject silent scorer under-coverage for public constraint fixtures.

    This is deliberately a narrow cross-component link check.  The manifest
    owns fixture vocabulary and the scorer owns evaluators, but neither may
    silently disagree after linking.
    """
    declared: set[str] = set()
    has_precedence = False
    for path, content in files.items():
        if not path.startswith("materials/") or not path.endswith(".json"):
            continue
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            continue
        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict):
                continue
            has_precedence = has_precedence or bool(case.get("precedence"))
            for constraint in case.get("constraints") or []:
                if isinstance(constraint, dict) and isinstance(constraint.get("type"), str):
                    declared.add(constraint["type"])
    if not declared:
        return []

    source = files.get("scorer.py", "")
    try:
        tree = ast.parse(source, filename="scorer.py")
    except SyntaxError:
        return []
    supported = _literal_string_set(tree, "SUPPORTED_CONSTRAINT_TYPES")
    errors: list[str] = []
    if supported is None:
        errors.append(
            "Scorer semantics: scorer.py must declare literal SUPPORTED_CONSTRAINT_TYPES for public fixture constraints"
        )
    else:
        missing = sorted(declared - supported)
        if missing:
            errors.append(f"Scorer semantics: public constraint types lack evaluators: {missing}")
    if "unsupported constraint type" not in source:
        errors.append("Scorer semantics: unsupported constraint types must fail closed, not be silently skipped")
    function_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if has_precedence and "_effective_constraints" not in function_names:
        errors.append("Scorer semantics: precedence-bearing fixtures require _effective_constraints lowering")
    return errors


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def registered_env_tools(source: str) -> set[str]:
    """Extract @env_tool(name=...) registrations exactly as EnvLoader observes them."""
    try:
        tree = ast.parse(source, filename="core.py")
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or _decorator_name(decorator.func) != "env_tool":
                continue
            name = next((kw.value for kw in decorator.keywords if kw.arg == "name"), None)
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                names.add(name.value)
    return names


def registered_mcp_tools(source: str) -> set[str]:
    """Extract FastMCP @mcp.tool() function names from a stdio bridge."""
    try:
        tree = ast.parse(source, filename="mcp_server.py")
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            func = call.func if call else decorator
            if isinstance(func, ast.Attribute) and func.attr == "tool":
                names.add(node.name)
    return names



def validate_scorer_abi(source: str, dimension_ids: set[str]) -> list[str]:
    """Reject scorer shapes that crash or disappear on incomplete attempts."""
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename="scorer.py")
    except SyntaxError:
        return errors
    score_defs = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "score"
    ]
    if not score_defs:
        return errors
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant) and key.value == "value"
                    and isinstance(value, ast.Constant) and value.value is None
                ):
                    errors.append("AgentOctagon scorer ABI: score rows must never use value=None")
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"int", "float"}
            and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "result"
        ):
            errors.append("AgentOctagon scorer ABI: optional result must be type/None-guarded before numeric conversion")
    string_literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    missing = sorted(dimension_ids - string_literals)
    if missing:
        errors.append(f"AgentOctagon scorer ABI: scorer source does not declare every rubric dimension: {missing}")
    return sorted(set(errors))

def validate_bundle_abi(*, meta: dict[str, Any], files: dict[str, str], ir: EnvironmentIR) -> list[str]:
    """Mechanically reject bundles that current AgentOctagon cannot dispatch."""
    errors: list[str] = []
    materials = meta.get("materials")
    if not isinstance(materials, dict):
        errors.append("AgentOctagon ABI: meta.yaml materials must be an audience mapping, not a list")
        agent_entries: list[Any] = []
    else:
        agent_entries = materials.get("agent") or []
        if not isinstance(agent_entries, list):
            errors.append("AgentOctagon ABI: materials.agent must be a list")
            agent_entries = []
    mounted: set[tuple[str, str]] = set()
    for entry in agent_entries:
        if not isinstance(entry, dict) or not entry.get("path"):
            errors.append("AgentOctagon ABI: each materials.agent entry requires local path and target")
            continue
        path = str(entry["path"])
        target = str(entry.get("target") or Path(path).name)
        mounted.add((path, target))
        if path not in files and not any(candidate.startswith(path.rstrip("/") + "/") for candidate in files):
            errors.append(f"AgentOctagon ABI: mounted material path is absent from bundle: {path}")
    required_mounts = set()
    for material in ir.materials:
        if material.visibility == "agent":
            required_mounts.add((material.target, material.target))
    for artifact in ir.artifacts:
        if artifact.schema_path:
            required_mounts.add((artifact.schema_path, artifact.schema_path))
    for path in files:
        if path.startswith("schemas/"):
            required_mounts.add((path, path))
    missing_mounts = sorted(required_mounts - mounted)
    if missing_mounts:
        errors.append(f"AgentOctagon ABI: agent-visible material/schema mounts missing: {missing_mounts}")

    expected_tools = {tool.tool_id for tool in ir.tools if tool.ownership == "benchmark_environment"}
    meta_tools = {
        str(tool.get("tool_id") or tool.get("name"))
        for tool in meta.get("tools", [])
        if isinstance(tool, dict) and (tool.get("tool_id") or tool.get("name"))
    }
    core_tools = registered_env_tools(files.get("core.py", ""))
    if expected_tools != core_tools:
        errors.append(
            "AgentOctagon ABI: core.py @env_tool registry mismatch: "
            f"expected={sorted(expected_tools)}, registered={sorted(core_tools)}"
        )
    if expected_tools != meta_tools:
        errors.append(
            "AgentOctagon ABI: meta.tools differs from benchmark_environment registry: "
            f"expected={sorted(expected_tools)}, meta={sorted(meta_tools)}"
        )

    if expected_tools:
        raw_mcp = (meta.get("entrypoints") or {}).get("mcp")
        if not isinstance(raw_mcp, dict) or raw_mcp.get("enabled") is not True:
            errors.append("AgentOctagon ABI: benchmark tools require entrypoints.mcp.enabled=true")
        else:
            command = raw_mcp.get("command")
            expected_command = ["python", "mcp_server.py"]
            if command != expected_command:
                errors.append(
                    "AgentOctagon ABI: MCP command must be relocatable from env.env_dir: "
                    f"expected={expected_command!r}"
                )
        mcp_source = files.get("mcp_server.py", "")
        mcp_tools = registered_mcp_tools(mcp_source)
        if expected_tools != mcp_tools:
            errors.append(
                "AgentOctagon ABI: mcp_server.py FastMCP registry mismatch: "
                f"expected={sorted(expected_tools)}, registered={sorted(mcp_tools)}"
            )
        required_bridge_tokens = {
            "OCTAGON_ATTEMPT_ID", "OCTAGON_ENV_TOKEN", "OCTAGON_BASE_URL", "/attempts/",
        }
        missing_tokens = sorted(token for token in required_bridge_tokens if token not in mcp_source)
        if missing_tokens:
            errors.append(f"AgentOctagon ABI: MCP bridge lacks authenticated attempt routing: {missing_tokens}")
    scorer_source = files.get("scorer.py", "")
    errors.extend(validate_scorer_abi(
        scorer_source, {criterion.criterion_id for criterion in ir.rubric.criteria}
    ))
    errors.extend(validate_constraint_semantics(files))
    if ir.artifacts and ("skill_workspace" not in scorer_source or "env_db" not in scorer_source):
        errors.append(
            "AgentOctagon scorer ABI: workspace artifacts/materials must resolve from Path(env_db).parent / skill_workspace"
        )
    tool_trace_required = any(
        evidence.source_type == "tool_trace"
        for evidence in ir.evidence
    )
    if tool_trace_required:
        if "tool_name" not in scorer_source or not any(tool.tool_id in scorer_source for tool in ir.tools if tool.ownership == "benchmark_environment"):
            errors.append(
                "AgentOctagon scorer ABI: tool evidence must consume trace list rows by tool_name/canonical tool id"
            )
    return errors
