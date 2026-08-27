# Benchmark Forge MVP 设计

## 1. 核心设计原则

### 1.1 Benchmark 是唯一中心对象

整个流程围绕 `Benchmark` 演化，而不是围绕 Pipeline 的局部返回值传递。

```text
Agent 读取 Benchmark
  → 产生结构化 Action / Patch / Assessment
  → Reducer 修改 Benchmark
  → Event Store 记录事件
  → Graph 决定下一节点
```

Agent 不直接返回完整 Benchmark，也不直接修改共享对象。

### 1.2 Agent 输出与领域状态分离

每个 Agent 只能输出自己的动作结果：

- Design：`DesignAction`；
- Grounding：`GroundingAction`；
- Allocation：`AllocationDecision`；
- Executor：`ItemCandidate`；
- Verification：`QualityAssessment`。

Reducer 负责把这些输出应用到 Benchmark，并执行确定性业务约束。

### 1.3 结构约束与语义约束分离

```text
PydanticAI
  └── 输出类型、字段、枚举、参数和局部验证

Domain Validator
  └── quota、容量、引用存在性、状态转换等业务约束

Verification & Control
  └── answerability、faithfulness、uniqueness、difficulty 等检查与补足控制
```

任何一层失败都不能静默通过，但 MVP 允许在记录失败原因后继续生成 degraded artifact。只有会破坏状态一致性、来源真实性或 artifact 可加载性的错误才是硬失败。

### 1.4 受约束自治

Agent 可以动态选择下一步，但必须受到以下约束：

- 允许的 action 集合；
- 工具参数 schema；
- 每个 Agent 最大 step 数；
- 每个 run 最大调用预算；
- 状态转换规则；
- 输出 artifact schema；
- 明确的终止条件。

## 2. 领域对象

### 2.1 Benchmark

```python
class Benchmark(BaseModel):
    benchmark_id: str
    schema_version: str
    user_goal: UserGoal
    dimensions: list[BenchmarkDimension]
    groundings: list[BenchmarkGrounding]
    allocations: list[BenchmarkAllocation]
    items: list[BenchmarkItem]
    status: BenchmarkStatus
    evidence: BenchmarkEvidence
    events: list[BenchmarkEvent]
    manifest: BenchmarkManifest
```

### 2.2 BenchmarkDimension

Dimension 表示 benchmark 要测量的一项能力，而不是简单的流程子任务。

必须包含：

- 唯一 id；
- 名称和描述；
- capability 标签；
- 所需模态；
- answer type；
- 目标约束；
- 当前状态。

### 2.3 BenchmarkGrounding

Grounding 表示一个 dimension 如何落到可执行的资源策略上。它不等价于“必须在现有 dataset pool 中找到数据集”。

Grounding 支持以下 source mode：

```text
existing_dataset   已有数据集
synthetic          程序化或模型辅助合成数据
hybrid             少量真实数据 + 合成/变换数据
pending            需要未来接入的数据源
blocked            当前无法执行，但保留设计结果
```

必须包含：

- dimension id；
- source mode；
- dataset id 或 source provider id；
- transformation / generation plan；
- estimated capacity；
- alignment / robustness / signal preservation；
- answerability / uniqueness；
- dry-run 或 synthetic validation 证据；
- `ready` / `partial` / `pending` / `blocked` 状态；
- accept/reject/defer 决策及理由。

Grounding 的硬约束是：不能伪造资源存在，并且必须记录证据强度。MVP 允许 provisional、pending 或 blocked grounding，不要求每个 dimension 当前都立即可执行。

### 2.4 BenchmarkItem

Item 必须包含：

- item id；
- dimension id；
- dataset id；
- question；
- context 或 media；
- options（如适用）；
- answer；
- source refs；
- generation claims；
- quality assessment；
- control history；
- accepted/rejected 状态。

## 3. 角色职责

### 3.1 Design Agent

只负责 `Benchmark.dimensions`。

允许动作：

```text
add_dimension
revise_dimension
discard_dimension
finish_design
request_grounding
```

禁止：直接生成最终 item、直接改 allocation、直接宣称 item 合格。

### 3.2 Grounding Agent

负责把 dimension 映射到可执行的资源策略，而不是单纯依赖现有 dataset pool。

典型行为：

```text
检查现有数据集
  → 若有数据：查看 schema、抽样、dry-run transformation
  → 若数据不足：评估 synthetic / procedural provider
  → 若当前无法实现：生成 pending 或 blocked grounding
  → 返回 ready / partial / pending / blocked 状态
```

Grounding Agent 必须区分三种情况：

1. `ready`：当前可以直接交给 Allocation 和 Executor；
2. `partial`：有一部分资源可执行，剩余 quota 需要补足或合成；
3. `pending/blocked`：当前不能执行，但不应自动删除 Design Agent 已提出的 dimension。

只有当用户明确要求“本次必须产出完整 benchmark”时，资源不足才会导致 run 失败；否则系统应输出可继续编辑的 draft。

### 3.3 Allocation Agent

负责在 `ready` 或 `partial` grounding 上分配 quota，并区分当前可执行 quota 与 deferred quota。

确定性约束至少包括：

- 总 quota 等于 target size；
- quota 不超过 grounding capacity；
- 每个 active dimension 达到最低比例；
- 每个 allocation 引用可执行或已明确 deferred 的 grounding；
- executable quota 不超过当前 capacity；
- deferred quota 必须有 source mode 和补足策略；
- 不允许重复 allocation key。

### 3.4 Executor Agent（Sample Realization）

Executor 负责按照 Allocation Agent 产生的 dataset-level transformation plan，逐个读取原始样本并生成 benchmark item。

它不是新增角色，而是原项目中的 Sample Realization / Executor 角色。

典型行为：

```text
读取 allocation 和原始样本
  → 观察当前 SampleState
  → 选择 transformation tool
  → 执行工具
  → 将 observation 写回 SampleState
  → 继续、完成或标记失败
```

Executor 可以使用 PydanticAI 进行每一步 action 的结构化决策，但最终 item 仍然必须经过 Verification & Control。

### 3.5 Verification & Control

Verification & Control 对 Executor 产出的 item 执行验证，并负责原项目中的配额补足和失败控制。MVP 中它首先是一个观察和标注角色，不是强制质量闸门。

它不是新增角色，而是原有 Verification & Control 角色的 agent 化实现。

至少检查：

- schema；
- answerability；
- faithfulness；
- answer uniqueness；
- answer leakage；
- answer type；
- source reference completeness；
- quota 是否满足。

检查失败后，由 Verification & Control 根据失败类型决定；MVP 允许先保留带 warning 的 candidate：

```text
标记 verified
标记 accepted_with_warnings
标记 rejected
要求 Executor 对当前样本重跑
重新选择原始样本
触发 quota replenishment
终止当前 allocation
```

所有控制动作都必须有固定上限和事件记录；MVP 不要求每个失败都进入修复循环。当前已实现 bounded replenishment：Verification & Control 生成 request，Executor 从 provider 的后续 offset 取样，最多执行 `max_item_attempts` 轮。

## 4. MVP Graph

> MVP 实现说明：当前使用线性 typed graph 加阶段内的降级处理和 checkpoint。
> `grounding → design`、`verification → executor` 等真正的条件回路保留在设计目标中，
> 待模型驱动 Agent 能返回明确 control action 后再开启；MVP 不因为缺少这些回路而阻断 artifact 输出。

```text
START
  ↓
design
  ↓
grounding
  ├── grounding_failed → design
  └── grounding_passed
          ↓
      allocation
          ├── infeasible → allocation
          ├── no immediate source → draft / pending
          └── feasible or partial
                  ↓
              execute_samples
                  ↓
                verification
          ┌───────┼────────┐
          │       │        │
       control  replenish  accept
          │       │        │
          └→ verification  └→ execute_samples

accept → END
```

所有回路和 deferred 路径都必须有：

- 最大循环次数；
- 失败原因；
- 事件记录；
- 明确的最终 fallback。

## 5. PydanticAI 使用规范

每个角色必须显式声明：

```python
Agent(
    model=...,
    deps_type=...,
    output_type=...,
    instructions=...,
)
```

约束：

1. 禁止使用无 schema 的 `dict[str, Any]` 作为 Agent 顶层输出；
2. 顶层输出必须是 Pydantic model 或明确的 union；
3. output validator 只负责局部结构和一致性；
4. 业务约束由 reducer/domain validator 执行；
5. retry 必须有上限；
6. 不持久化隐藏 chain-of-thought，只持久化 action、summary、evidence 和错误；
7. 工具必须声明参数类型，并通过依赖注入获得数据集池、预算和运行上下文。

## 6. Artifact 与 Evidence

最终输出至少包括：

```text
benchmark.json
manifest.json
events.jsonl
items/<item_id>.json
```

每个 item 必须能追溯：

```text
item
  → source refs
  → grounding
  → dimension
  → executor actions
  → verification results
  → control attempts
```

## 7. 与 AgentEval 的边界

Benchmark Forge 负责：

- 生成 benchmark；
- 记录生成过程；
- 生成候选质量评估；
- 导出 artifact。

AgentEval 负责：

- 对 benchmark 或 agent 产物进行独立评估；
- 记录 rubric/skill 结果；
- 生成 evidence report；
- 进行历史和质量分析。

Benchmark Forge 可以依赖 AgentEval 的协议思想，但不能复制其 evaluator runtime。

## 6. Scorer 也是 Agent 产物

`scorer.py` 不再只由 Executor 一次性生成后交给机械静态规则判断。
Verification & Control 先产生 typed `ScorerDesign`，为每个评分维度提出多种
可观测实现和证据降级方案；Executor 根据该设计实现；Verification & Control
再产生 `ScorerReview`，必要时要求 Executor 进行有界修复。完整协议见
`docs/SCORER_DESIGN.md`。

## 7. Preference Alignment（实现前规划选择）

Benchmark Forge 在规划完成、环境 materialization 之前增加 Preference Alignment
角色。系统使用同一 prompt 和同一模型独立生成两个 `BenchmarkPlanCandidate`，不通过
复杂差异 prompt 制造变化。Preference Alignment Agent 使用 Preference Registry
返回的有限人类偏好证据，对分结构 criterion 预测人类选择，并决定 select、revise、
regenerate 或 abstain。人类对齐只在离线批处理中由独立 scheduler/operator 采样，运行中
Agent 不得请求或等待人类介入。具体协议见：

- `docs/PREFERENCE_ALIGNMENT.md`
- `docs/PREFERENCE_ALIGNMENT_ACCEPTANCE.md`

Preference Alignment 不替代 Verification & Control，也不拥有 Preference Library。

## 8. 平级模块与产物接口原则

`benchmark-forge`、`eval-system`、`agent-eval` 是平级模块，不构成运行时的互相调用关系：

```text
benchmark-forge
  输出 Benchmark Bundle / rubric / evidence requirements
        ↓ 文件或版本化产物
 eval-system
  消费 Benchmark Bundle，输出 attempt / artifacts / runtrace / verifier result
        ↓ 文件或版本化产物
 agent-eval
  消费 eval-system 产物，输出评分与分析报告
```

模块之间只依赖版本化接口和产物格式，不依赖对方的 Python 内部类、数据库连接、服务进程或私有目录。任何编排脚本都只能负责搬运和记录产物，不能把一个模块变成另一个模块的调用者。

在 Benchmark Forge 内部，Design、Grounding、Allocation、Executor、Verification 与 Component Agent 也保持职责平级；它们通过 Contract、IRDraft、ComponentOutput、QualityAssessment 等结构化产物合作。Linker 是接口一致性检查器，不是业务角色的上级替代品。

## 9. Rubric 的目标边界

Rubric 不是为每个 benchmark 生成一套复杂的验收状态机。它只需要稳定表达：

```text
测量对象是什么
检查范围是什么
需要观察哪些公开事实
什么情况算偏离目标
```

Rubric review 的重点是发现：

- 检查项是否从原始能力目标偏移；
- 检查范围是否明显过大，混入了未声明能力；
- 检查范围是否明显过小，无法覆盖目标行为；
- 是否把目标行为反向解释成相反结论；
- 是否把不存在的事实、隐藏答案或额外标准加入评分。

Rubric 不需要细化到每个工具调用的复杂程序逻辑。能由运行时确定的事实由 `eval-system` 产生；需要语义判断的部分由 `agent-eval` 的通用 evaluator 根据 rubric 评价。Benchmark Forge 只负责生成并静态检查 rubric 与 Benchmark Contract/IR 的一致性。
