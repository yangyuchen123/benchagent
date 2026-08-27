# Benchmark Forge

Benchmark Forge 是一个实验性的新项目：使用 PydanticAI 驱动角色 Agent，围绕中心对象 `Benchmark` 生成可验证、可审计的 benchmark。

当前项目已进入 MVP 实现阶段；目标、设计和验收标准仍作为实现约束。

## 文档

- [项目目标与边界](docs/PROJECT_CHARTER.md)
- [MVP 设计](docs/DESIGN.md)
- [验收标准与预期行为](docs/ACCEPTANCE.md)
- [Preference Alignment 目标架构](docs/PREFERENCE_ALIGNMENT.md)
- [Preference Alignment 验收标准](docs/PREFERENCE_ALIGNMENT_ACCEPTANCE.md)
- [实施过程、问题与验证记录](docs/IMPLEMENTATION_JOURNEY.md)
- [Environment IR 与组件组装](docs/ENVIRONMENT_IR.md)
- [自动 Contract→IR→组件修复流程](docs/AUTONOMOUS_MATERIALIZATION.md)
- [角色职责问题审计](docs/ROLE_RESPONSIBILITY_GAPS.md)

## 设计摘要

```text
Benchmark
  → Design Agent
  → Grounding Agent
  → Allocation Agent
  → Executor planning branches A/B（目标架构）
  → Preference Alignment（目标架构，尚未实现）
  → Executor materialization
  → Verification & Control
  → Verified / Partial / Degraded Benchmark Artifact
```

## 当前实现

第一批 MVP 代码已经加入 `src/benchmark_forge/`。当前代码仍是原五角色实现；Preference Alignment 是已冻结但尚未实施的扩展：

- `domain.py`：Benchmark、dimension、grounding、allocation、item、event schema；
- `actions.py`：五个原有角色的结构化 action/result contract；
- `reducers.py`：只允许通过 reducer 修改 Benchmark；
- `providers.py`：existing dataset 与 procedural/synthetic source provider；
- `orchestrator.py`：Design → Grounding → Allocation → Executor → Verification & Control；
- `graph.py`：基于 `pydantic-graph` 的同一五角色 typed graph；
- `pydantic_ai_adapter.py`：可选的 PydanticAI role runner 边界；
- `persistence.py`：`benchmark.json` 和 `events.jsonl` artifact；
- `checkpoint.py`：阶段 checkpoint 和 resume；
- Verification & Control 失败后可生成 bounded replenishment request，由 Executor 消费 provider 的后续 offset。

当前默认使用 deterministic MVP agents，目的是先验证状态流转、降级行为和 artifact 契约，不需要 API key。

```bash
PYTHONPATH=src python -m benchmark_forge.cli \
  --goal "测试模型的多文档推理能力" \
  --target-size 10 \
  --source procedural \
  --artifact-root run/demo
```

PydanticAI 的五角色 model-backed adapter 已加入 `pydantic_agents.py`。它只负责调用模型并返回结构化 role result，Benchmark 状态仍由 reducer/orchestrator 管理；当前 CLI 默认仍使用 deterministic agents。


## Checkpoint / resume

运行时可以在每个阶段写入 checkpoint：

```python
benchmark = BenchmarkOrchestrator().run(
    goal,
    providers,
    artifact_root="run/demo",
)

resumed = BenchmarkOrchestrator().run(
    goal,
    providers,
    resume_from="run/demo/checkpoint.json",
    artifact_root="run/resumed",
)
```

MVP 当前在 Design、Grounding、Allocation、Executor、Verification、Replenishment 之后保存阶段状态。
恢复时会从 checkpoint 中的 `next_stage` 继续，不会重新覆盖之前的合法 Benchmark state。

## Preference Alignment / double planning（Phase 1）

当前已提供独立的 pre-materialization contract：

```python
from benchmark_forge import DoublePlanningService, MaterializationGate
```

它实现：

```text
same prompt → Plan A/B → similarity check → bounded same-prompt resample
→ PreferenceAlignmentDecision → MaterializationGate
```

当前已通过 `BenchmarkGenerationService.generate_with_alignment()` 提供 opt-in 接入；旧
`BenchmarkGenerationService.generate()` 和 `BenchmarkOrchestrator.run()` 仍保持兼容。
新入口会先生成两个 branch、转换为 `BenchmarkPlanCandidate`、执行 alignment gate，
只有 selected branch 才会进入 environment materialization。

## PlanningAlignmentPipeline

当前可以独立运行规划选择闭环：

```python
from benchmark_forge import (
    DoublePlanningService,
    PlanningAlignmentPipeline,
)

result = PlanningAlignmentPipeline(
    planner=double_planner,
    evidence_client=registry_evidence_client,
    decider=preference_alignment_agent,
).run(
    goal=goal,
    prompt=prompt,
    context_key="subagent-coordination",
    subject_type="benchmark-plan",
)

if result.selected_plan is not None:
    # 只有这里的 plan 才允许进入 materialization
    ...
```

该 pipeline 目前是显式 opt-in，不会改变旧 `BenchmarkOrchestrator.run()` 的默认行为。

## Agent Capacity Library

能力构造库位于：

```text
benchmark-forge/src/benchmark_forge/capacity_library.py
benchmark-forge/docs/AGENT_CAPACITY_LIBRARY.md
```

当前包含 12 项 Agent 能力，并能将能力定义转换为 `BenchmarkPlanCandidate` intent。
能力库不会把任务压缩成选择题，也不会直接生成环境目录。


## Autonomous materialization replay

已接受的 Contract 可以在不重做 Design/Grounding/Allocation 的情况下自动回放：

```bash
.venv/bin/python scripts/replay_contract_autonomously.py \
  path/to/benchmark.json \
  --output-root run/fixed-contract-replay \
  --run-local-tests
```

流程会按 Contract/IR checksum 重用成功产物，并在 linker、静态验证或 bundle tests
失败时优先进行机械 ownership 定位；歧义失败由受限 Diagnosis Agent 分配给
Manifest、Runtime、Scorer 或 Tests，然后只修复责任组件。每次运行写入
`workflow-report.json` 和 `workflow-runs/<run-id>/`，可使用：

```bash
.venv/bin/python scripts/summarize_materialization_runs.py run/
```

统计 ready rate、零人工介入率、各阶段失败率和组件重用情况。生产环境的生成代码
执行仍应由平级 `eval-system` 隔离完成；`--run-local-tests` 仅用于开发自测。

## Environment IR 1.3 artifact schema

对于被 Contract 描述为 structured/schema-valid 的 JSON artifact，IR Compiler 现在必须补全：

```text
IRArtifact.schema_path
IRArtifact.schema_def
required_feature=artifact_schema
```

Manifest、Runtime、Scorer、Tests 共同消费这一个 Frozen IR binding。静态门禁会拒绝
`{type: object}`、仅 `minProperties` 的任意对象，以及无法表达 data-dependent all-case
coverage 的 schema。当前固定 Contract 真实重放产物位于：

```text
run/artifact-schema-case-coverage-replay-20260827/bundle/
```

该 Bundle 已通过 linker/static validation；生成代码的动态执行与正式 Agent pilot 仍由平级、
隔离的 `eval-system` 完成。

## AgentOctagon runtime ABI

生成 Bundle 现在显式适配 `agent-octagon.env-loader.v1`：

```text
materials.agent path/target mounts
core.py @octagon.env_api.env_tool
FastMCP authenticated attempt bridge
env_db Path / trace list scorer ABI
numeric-total failure scoring
```

静态门禁和可信跨项目 loader integration test 见：

```text
src/benchmark_forge/agent_octagon_abi.py
tests/test_agent_octagon_abi.py
docs/AGENT_OCTAGON_RUNTIME_ABI.md
```

首次故障 run 已标记为 infrastructure-invalid，不用于模型排名。修复后的 Bundle 仍需
由隔离 AgentOctagon/eval-system 重新动态运行后才能产生正式分数。
