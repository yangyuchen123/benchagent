# AgentOctagon Runtime ABI Boundary

## 1. 目的

Benchmark Forge、AgentOctagon、eval-system 是平级项目。Benchmark Forge 不调用 AgentOctagon，但生成的 Bundle 必须实现 AgentOctagon 当前可消费的文件 ABI。

版本标识：

```text
agent-octagon.env-loader.v1
```

该 ABI 不是 Benchmark Contract 的业务语义；它是 Materialization 的目标运行时协议。

## 2. Materials ABI

当前 AgentOctagon `run_dispatch._material_entries()` 接受：

```yaml
materials:
  agent:
    - path: materials/input.json
      target: materials/input.json
    - path: schemas/final_output.schema.json
      target: schemas/final_output.schema.json
```

含义：

```text
path   = Bundle/environment directory 内的源路径
target = attempt skill_workspace 内的目标路径
```

禁止把 typed IR registry 直接写进 `meta.yaml.materials`：

```yaml
materials:
  - material_id: ...   # 当前 loader 不接受
```

完整 typed 信息保留在：

```yaml
material_contracts:
  - material_id: ...
    source_type: generated
    ...
```

所有 Agent 可见 material 和公开 `schemas/*` 都必须有 mount entry。

## 3. Tool Registration ABI

`meta.yaml.tools` 只是声明，不能注册工具。AgentOctagon EnvLoader 在 import `core.py` 时只收集：

```python
from octagon.env_api import EnvContext, env_tool

@env_tool(
    name="validate_output",
    description="...",
    parameters={...},
)
def validate_output(ctx: EnvContext, path: str) -> dict:
    ...
```

必须满足：

```text
Frozen IR benchmark_environment tool IDs
= meta.yaml.tools
= core.py @env_tool registry
= mcp_server.py @mcp.tool registry
```

只在 `core.py` 写普通函数或自定义 `dispatch()` 不会被 EnvLoader 注册。

## 4. MCP / Authentication ABI

当前 Claude/Codex adapter 只有在以下入口存在时才注入 MCP 和 attempt 凭证：

```yaml
entrypoints:
  mcp:
    enabled: true
    transport: stdio
    name: octagon-<environment-id>
    command:
      - python
      - mcp_server.py
```

这里的 `python` 是 runtime ABI token，不是 Forge 项目解释器路径。AgentOctagon
消费该声明时必须：

```text
python/python3 → AgentOctagon 当前 sys.executable
cwd            → env.env_dir
```

禁止假定环境安装在：

```text
<project-root>/envs/<environment-id>
```

否则独立挂载的 `.../candidate/bundle` 会在 MCP initialize 前因脚本路径错误退出。
Forge 也不能使用 `uv run --project .` 绑定自身项目依赖；MCP 依赖由消费 Bundle
的 AgentOctagon runtime 提供。

MCP bridge 必须读取：

```text
OCTAGON_ATTEMPT_ID
OCTAGON_ENV_TOKEN
OCTAGON_BASE_URL
```

并代理：

```text
POST {OCTAGON_BASE_URL}/attempts/{attempt_id}/tools/{tool_id}
Authorization: Bearer {OCTAGON_ENV_TOKEN}
```

Agent 不应自行发现或直接拼接内部 endpoint。401 通常说明 Bundle 没有走受支持的 MCP 注入路径。

## 5. Scorer ABI

AgentOctagon 调用：

```python
score(
    attempt_id=str,
    task=dict,
    env_db=Path(".../attempts/<id>/env.db"),
    trace=list[dict],
    final_state=dict,
)
```

Artifact/material workspace：

```python
workspace = Path(env_db).parent / "skill_workspace"
```

工具证据是 trace list row：

```json
{
  "tool_name": "validate_output",
  "arguments": {...},
  "result": {...},
  "is_error": false
}
```

Scorer 不得假设：

```text
env_db 是 material_registry/artifact_registry mapping
trace 是 {events: [...]}
```

每个返回 row 必须有数值型 `value`。以下路径均不得抛异常：

```text
artifact missing
artifact malformed
validator not called
validation result missing
trace empty/partial
timeout
required case missing
```

Agent failure 返回数值低分；environment material/schema 缺失返回同样数值安全的 row，并附：

```text
status=invalid_environment
```

当前 AgentOctagon 聚合器尚不会把 row status 自动提升为 infrastructure status，因此正式统计仍需 eval-system/运行治理层读取该状态。

## 6. Forge 门禁

`validate_bundle_abi()` 机械检查：

```text
materials 是 audience mapping
path/target 源文件存在
所有公开 material/schema 已挂载
core @env_tool 与 IR 一致
meta.tools 与 IR 一致
FastMCP tool 与 IR 一致
MCP 使用 attempt ID/token/base URL
MCP command 是 env-local `python mcp_server.py`
scorer 不返回 value=None
scorer 不对 optional result 直接 int/float
scorer 从 env_db.parent/skill_workspace 读取产物
scorer 按 tool_name 读取 trace list
```

Promotion 必需检查新增：

```text
agent_octagon_abi
```

## 7. Integration Test

`tests/test_agent_octagon_abi.py` 使用测试内可信代码，通过 AgentOctagon 自身 `.venv` 实际执行：

```text
EnvLoader.load_all
→ tool registry
→ _material_entries
→ _copy_agent_materials
→ _mcp_server_specs
→ RegisteredTool.call
```

当前可信 integration 还断言：

```text
McpServerSpec.cwd == env.env_dir
cwd / mcp_server.py 存在
真实 MCP initialize 成功
tools/list == [validate_output]
```

该测试不会执行 LLM 生成代码。真实生成 Bundle 仍必须由隔离 eval-system/AgentOctagon pilot 动态验证。
