# Agent Capacity Library

状态：`phase-1-implemented`

## 1. 目标

Agent Capacity Library 是 Benchmark Forge 的**能力构造库**，不是 benchmark 题库，也
不是模型排行榜。它把需要评测的 Agent 能力定义成可复用的 construct、observable
behavior、failure mode、evidence source 和 scoring intent，供 Design、双规划和
Preference Alignment 使用。

当前初始能力集合：

| ID | 中文 | 核心可观测对象 |
|---|---|---|
| `instruction_following` | 指令遵从 | 多约束执行、冲突处理、输出协议 |
| `aesthetic_quality` | 美学能力 | 作品整体性、目的适配、迭代改进、人类偏好 |
| `self_tool_building` | 自制工具能力 | 工具缺口识别、实现、测试、集成 |
| `reflection` | 反思能力 | 错误定位、因果分析、修复与行为改变 |
| `hallucination_control` | 幻觉控制 | 证据边界、验证、校准、拒答 |
| `long_horizon_durability` | 长任务耐久力 | 长阶段目标保持、检查点、恢复、后期质量 |
| `robustness_fault_tolerance` | 鲁棒性/容错 | 故障检测、恢复、状态完整性、重试纪律 |
| `efficiency` | 效率 | 质量约束下的时间、工具和资源成本 |
| `context_compression_fidelity` | 上下文压缩保真度 | 事实、约束、关系和不确定性保留 |
| `memory_selection_accuracy` | 记忆选择准确性 | 相关记忆检索、来源加权、污染抵抗 |
| `autonomous_termination_self_evaluation` | 自主终止与自我验收 | 完成判断、自检、停止时机、未完成项识别 |
| `delegation_quality` | 任务委派质量 | 拆解、指派、依赖、权限、验收、整合 |

对应代码：

```text
benchmark-forge/src/benchmark_forge/capacity_library.py
```

默认库：

```python
from benchmark_forge import DEFAULT_CAPACITY_LIBRARY
```

## 2. 每项能力的契约

每个 `CapacityDefinition` 必须包括：

```text
capability_id
name_zh / name_en
description
construct_definition
observable_behaviors
anti_patterns
recommended_task_forms
required_environment_features
evidence_sources
scoring_dimensions
perturbations
prerequisite_capabilities
human_preference_relevance
default_difficulty
version
```

能力定义只描述**测量构造**，不直接生成具体任务。具体任务必须由 Forge 的 Plan 和
Environment 阶段实现。

## 3. Benchmark 设计规则

默认规则：

- 除非用户明确要求静态知识测试，能力 benchmark 默认使用 `executable_task`；
- 不允许把复杂 Agent 能力压缩成一道选择题；
- 至少需要一个开放任务、工具/环境交互和可观测 evidence；
- scoring intent 必须能映射到公开 artifact、trajectory、state transition 或 external check；
- failure injection 和 recovery path 应优先于只测 happy path；
- 人类偏好高相关能力（例如美学、委派、自主验收）应保留离线 preference 对齐入口；
- 不使用 Agent 自我报告作为唯一 ground truth；
- capability 之间允许组合，但组合后必须声明 primary/secondary capability。

## 4. 能力组合

推荐把能力分为：

```text
primary capability
  = 本次 benchmark 的主测量对象

secondary capabilities
  = 任务执行中不可避免的辅助能力
```

例如“subagent 任务委派”可以是：

```text
primary: delegation_quality
secondary:
  - instruction_following
  - robustness_fault_tolerance
  - autonomous_termination_self_evaluation
  - efficiency
```

不能因为一个任务出现了 memory 或 reflection 字样，就宣称它同时测量了所有能力。
每个 secondary capability 都必须有独立的 observable evidence，否则只记录为背景条件。

## 5. 与 Preference Alignment 的关系

Capacity Library 负责定义：

```text
应该测什么
应该观察什么
哪些失败算失败
```

Preference Alignment 负责比较：

```text
Plan A 是否比 Plan B 更符合已有的人类设计偏好
```

Preference Alignment 不得修改能力定义，也不能把某一个 Agent prediction 写成能力库
事实。工程师审核后的 preference summary 可以作为能力规划的离线 evidence。

## 6. 与现有五角色的关系

```text
Design
  → 从 Capacity Library 选择 primary/secondary capabilities

Grounding
  → 查找能支撑这些能力的 environment/tools/state

Allocation
  → 根据能力复杂度和资源预算分配任务规模

Executor
  → 按选中的 BenchmarkPlanCandidate 实现任务与环境

Verification & Control
  → 验证 evidence、scorer、可执行性和能力边界
```

Capacity Library 不替代五个原有角色，也不直接创建 environment bundle。

## 7. Phase 1 API

```python
spec = DEFAULT_CAPACITY_LIBRARY.benchmark_spec(
    "delegation_quality",
    intent="评测 Agent 对并行 subagent 任务的拆解、指派和验收",
)
plan = spec.to_plan_candidate(
    plan_id="delegation-plan-a",
    goal="评测 Agent 调用自己的 subagent 的任务拆解能力",
)
```

`to_plan_candidate()` 只返回 plan intent，字段 `plan_only=True`。它不会生成：

```text
meta.yaml
core.py
scorer.py
tasks/
materials/
tests/
```

这些仍由后续 Executor/materialization 流程完成。
