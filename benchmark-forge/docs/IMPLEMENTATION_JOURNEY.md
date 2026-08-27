# Benchmark Forge 实施过程、问题与验证记录

> 文档状态：2026-08-25
>
> 目的：记录从自然语言 benchmark 生成系统演进到 Agent-only、开放可执行 benchmark 生成系统的每个关键步骤，说明遇到的问题、采取的方案、为什么这样做，以及方案是否真正解决了问题。
>
> 本文不是理想架构说明，而是实现和实验的事实记录。对于“部分解决”和“尚未解决”的问题，不把设计完成误写成系统完成。

---

## 1. 总体目标与当前结论

系统的目标不是生成选择题，也不是让 LLM 只写一段任务描述，而是：

```text
自然语言目标
  → 能力维度
  → 可执行 Benchmark Contract
  → 完整环境 bundle
  → eval-system 执行
  → agent-eval 评分
```

对于 Agent 行为目标，最终 benchmark 应包含：

- 开放任务指令；
- 工具或环境入口；
- 可观察的运行状态；
- Agent trajectory / tool trace；
- 工作区 artifacts；
- 明确的评分维度和证据来源；
- 可以被 `eval-system` 运行的环境目录；
- 可以被 `agent-eval` 消费的 TrialResult 和评分证据。

### 当前最准确的验收结论

| 子系统 | 当前状态 |
|---|---|
| 原五角色状态机 | 通过 MVP 测试 |
| RAG 知识库 | 已接入，能提升设计复杂度和协议对齐 |
| 开放任务约束 | 已加入 prompt 和 schema 约束 |
| Agent Capacity Library | 已实现 |
| 双规划 | 已实现并通过真实 LLM 运行 |
| Preference Registry | 已实现并通过 HTTP/离线链路测试 |
| Preference Arena | 已实现 Registry-backed prototype；Label Studio 是可选适配器 |
| 真实 Preference Alignment | 已通过 `select_with_warnings` 和 `abstain` 实验 |
| Benchmark Contract 生成 | 已能生成 `executable_task` |
| 新环境完整组装 | 尚未稳定通过 |
| scorer 语义正确性 | 已发现真实 bug，但修复闭环尚未完成 |
| eval-system 执行 | 尚未完成真实闭环 |
| agent-eval 评分 | 尚未完成真实闭环 |
| 整体发布验收 | 未通过 |

当前真正的主阻塞点是：

```text
Benchmark Contract 已经设计出来
→ 无法稳定组装成完整、可执行、可评分的 benchmark bundle
```

---

## 2. 角色边界的建立

### 遇到的问题

原项目使用自然语言和旧式流程生成 benchmark，角色之间的责任边界不够清晰，容易出现：

- Design 同时设计任务和环境；
- Grounding 把文档误认为可执行环境；
- Executor 直接修改全局 Benchmark 状态；
- Verification 既验收又偷偷修复；
- 生成结果缺少可审计事件和阶段状态。

### 解决方案

保留项目原有的五个角色，并将职责固定为：

```text
Design Agent
  → 定义评测能力、任务形式和证据目标

Grounding Agent
  → 判断哪些资源能够承载这些目标

Allocation Agent
  → 分配 provider 容量、生成配额和短缺策略

Executor / Sample Realization Agent
  → 将 allocation 和 sample 变成 BenchmarkItem / Contract

Verification & Control Agent
  → 验证、接受、拒绝、补充或停止
```

Benchmark 状态只允许由 reducer/orchestrator 改变，Agent 只返回结构化 action/result。

### 为什么这样做

如果 Agent 可以直接改 Benchmark 状态，就无法区分：

- Agent 的提议；
- 系统实际接受的状态；
- Verification 的结论；
- 失败和补充请求。

保留原有角色也避免一次性重写整个系统，同时为后续 PydanticAI Agent 提供稳定的调用边界。

### 是否成功

**成功。**

Benchmark Forge 当前测试：

```text
47 passed
```

`Benchmark`、`BenchmarkEvent`、checkpoint、reducer 和状态流转可以独立测试，不需要真实 LLM。

---

## 3. 解决“开放 Agent 任务被压缩成选择题”

### 遇到的问题

旧 schema 主要围绕：

```text
question / options / answer
```

因此即使用户目标是：

- subagent 任务拆解；
- 工具调用；
- 并行调度；
- 多轮验收；
- 失败修复；

模型也可能把它压缩成一道静态题或选择题。

### 解决方案

增加 `executable_task` 形式，并在 Design/Executor prompt 中明确：

- Agent 行为默认使用 `task_form=executable_task`；
- multiple choice 不能替代工具行为；
- 任务必须有公开指令、环境、工具/入口、产物、评分维度和观察要求；
- subagent 任务必须明确 DAG、指派、交接、验收和 repair；
- 不能把最终答案作为唯一评分依据。

对应的领域结构包括：

```text
ExecutableTaskContract
├── instruction
├── context / constraints
├── agent_capabilities
├── coordination
├── environment
├── artifacts
├── scoring
└── observation_requirements
```

### 为什么这样做

开放 Agent benchmark 的构造对象不是“答案”，而是：

```text
任务 + 环境 + 工具 + 状态 + 产物 + 轨迹 + 评分证据
```

如果 schema 不表达这些内容，prompt 再好也只能依赖模型自觉，无法验证。

### 是否成功

**基本成功。**

真实运行中已经生成过：

```text
item_kind = executable_task
answer_type = agent_trajectory / open_ended
```

并且 Verification 能识别并拒绝被压缩成选择题的任务。

但这只解决了“设计出开放任务”，没有解决后续环境组装。

---

## 4. 引入 RAG 知识库

### 遇到的问题

不能把整个 `agent-octgan/env` 或 `agent-octagon-envs` 工作区挂载给 Agent：

- 磁盘和映射关系复杂；
- 只读权限管理困难；
- 私有目录、expected answer 和 scorer 可能泄露；
- 原始 prompt 容易爆上下文；
- Agent 无法区分资料、协议和可执行环境。

### 解决方案

建立只读 RAG Knowledge Base：

- 使用 SQLite/FTS5；
- 只索引公开 `meta.yaml`、README、SKILL、task、input、material；
- 排除 `private`、`.git`、缓存和环境实现细节；
- Agent 只拿到检索结果和引用，不直接挂载源目录；
- 明确检索结果是 precedent/evidence，不是 instruction。

不同角色按作用检索不同内容：

```text
Design
Grounding
Allocation
Executor
Verification
Materialization
```

### 为什么这样做

RAG 的作用不是代替 Agent 设计，而是让 Agent 了解：

- 现有环境协议如何表达；
- 工具入口是什么形式；
- 哪些 evidence 可以被评分；
- 过去环境如何处理 workspace 和 artifact；
- 什么样的任务是真正可执行的。

这样可以提高 benchmark 的目标对齐、结构复杂度和预期难度，同时避免给 Agent 原始目录权限。

### 是否成功

**部分成功，并有真实实验支持。**

加入知识库后观察到：

- benchmark 目标对齐提高；
- 人类设计规范对齐提高；
- 结构复杂度提高；
- 预期求解难度提高；
- 选择题化问题减少。

但当时发现：

```text
RAG 没有直接提高 artifact 生成成功率
```

原因不是知识不足，而是后续组装 Agent 的负担和多文件一致性问题仍然存在。

---

## 5. Grounding Agent 过度依赖数据集的问题

### 遇到的问题

早期 Grounding 逻辑假设：

```text
没有足够数据集 → 无法继续生成 benchmark
```

这对 Agent 行为 benchmark 不成立。一个新的开放任务可能只需要：

- 合成环境；
- 确定性工具；
- 小型 fixture；
- 公开任务契约；
- 可观察状态和评分器。

### 解决方案

放宽 Grounding 和 Allocation 门槛：

- raw dataset 不再是 executable benchmark 的必要条件；
- `generated_environment` 可以先以 `generated_contract` 或 `pending` 状态存在；
- 允许 `realization_quota`；
- 不支持执行时暂缓，而不是强行降级成静态题；
- generated environment 先生成契约，再由后续组装阶段物化。

### 为什么这样做

Agent 能力 benchmark 的关键不是数据规模，而是：

```text
是否有可执行的交互闭环和可信观察证据
```

### 是否成功

**成功解决了“无数据就无法继续”的流程阻塞。**

系统现在可以在没有已有数据集的情况下生成：

```text
generated_environment blueprint
ExecutableTaskContract
realization quota
```

但生成后如何可靠物化环境，仍然是当前主问题。

---

## 6. 环境协议和目录合法性

### 遇到的问题

曾经生成过非法环境名称：

```text
generated.launch-readiness-decomposition.v1
```

目录名带点号，无法作为当前环境目录规范下的合法 ID。

此外，早期流程只产生空壳 candidate，而不是完整目录：

```text
meta.yaml
core.py
scorer.py
tasks/
materials/
tests/
```

### 解决方案

统一环境 ID 规范：

```text
^[a-z0-9]+(?:-[a-z0-9]+)*$
```

例如：

```text
generated-launch-readiness-decomposition-v1
```

并增加 scaffold validation：

- 必须有 `meta.yaml`、`core.py`、`scorer.py`；
- 至少一个 `tasks/*.json`；
- 路径不能绝对路径或穿越；
- 文件大小有限制；
- `meta.yaml.name` 必须和 environment_id 一致；
- dimensions 必须和任务评分维度一致；
- Python 文件必须能解析。

### 为什么这样做

生成环境必须是后续 `eval-system` 可以发现和加载的真实目录，而不是只存在于 JSON 中的概念对象。

### 是否成功

**目录规范问题已解决。**

**完整环境问题尚未解决。**

当前已经有 `write_scaffold()` 和 `validate_scaffold()`，但它们只能验证 Agent 返回的 bundle，不能保证 bundle 的语义和运行时行为正确。

---

## 7. 双规划和 Preference Alignment

### 遇到的问题

为了提高 benchmark 与人类专家设计规范的对齐，需要在 materialization 前比较两个独立方案。

但存在几个风险：

- 如果 A/B 使用不同 prompt，就会混入 prompt 差异；
- 如果两个方案过于相似，人类偏好没有信息量；
- 如果没有足够人类证据，Agent 不应该凭常识代替人类偏好；
- 人类介入不能成为运行时阻塞路径。

### 解决方案

实现：

```text
same prompt
  ├── independent Plan A
  └── independent Plan B
        ↓
  similarity check
        ↓
  bounded same-prompt resample
        ↓
  offline Preference evidence
        ↓
  Preference Alignment Agent
        ↓
  MaterializationGate
```

Preference Alignment 只允许：

```text
select
select_with_warnings
revise
regenerate
abstain
```

不允许：

```text
request_human
human_requested
```

### 为什么这样做

人类对齐是离线数据，不应该让生成 Agent：

- 创建 assignment；
- 主动要求人类；
- 等待人类；
- 将 preference 当作正确性证明。

Preference 只回答：

```text
哪个设计更符合已有的人类偏好经验
```

不回答：

```text
哪个设计一定可执行、正确或评分有效
```

### 是否成功

**Preference Alignment 链路已验证成功。**

真实 LLM 实验中已经得到：

```text
冲突或不足证据 → abstain
足够离线证据 → select_with_warnings
```

一次真实结果：

```text
control_action = select_with_warnings
selected_plan = real-e2e-subagent-coordination-alignment-2
```

但这不是当前 benchmark 生成失败的根因。后续实验已证明，Plan 选出来以后，问题仍发生在环境组装阶段。

另外，直接要求工程师只评价复杂方案的 A/B 负担过高，因此 Preference Arena 初始方案暂时不作为主路径。

---

## 8. Agent Capacity Library

### 遇到的问题

自然语言目标经常只描述一个模糊能力，例如：

```text
评测 agent 的自主性
```

如果没有统一能力定义，生成的 benchmark 容易：

- 能力边界模糊；
- 任务和评分不对应；
- 难度不可比较；
- 只生成最终答案题。

### 解决方案

建立 Agent Capacity Library，当前包含：

```text
instruction_following
 aesthetic_quality
self_tool_building
reflection
hallucination_control
long_horizon_durability
robustness_fault_tolerance
efficiency
context_compression_fidelity
memory_selection_accuracy
autonomous_termination_self_evaluation
delegation_quality
```

每项能力记录：

- construct definition；
- observable behaviors；
- anti-patterns；
- recommended task forms；
- required environment features；
- evidence sources；
- scoring dimensions；
- perturbations；
- prerequisite capabilities；
- human preference relevance；
- default difficulty。

### 为什么这样做

能力库只生成：

```text
Benchmark Plan Intent
```

不会直接伪造：

```text
meta.yaml
core.py
scorer.py
```

这样可以保持“能力定义”和“环境实现”的边界。

### 是否成功

**成功。**

能力库可以生成公开的 `BenchmarkPlanCandidate`，并且不会把 Agent 行为退化成选择题。

但它只解决“测什么”的复用问题，不能解决环境组装。

---

## 9. 第一次真实完整 Forge 运行：发现组装瓶颈

### 实验流程

真实运行使用：

```text
PydanticAIRoleAgents
真实 gpt-5.6-luna
RAG Knowledge Base
双规划
Registry preference evidence
Preference Alignment
环境 staging
Scorer Design
Scorer Review
```

### 已成功的部分

真实流程推进到了：

```text
五角色 benchmark branch
→ 双规划
→ Registry evidence
→ Preference Alignment
→ selected branch
→ environment candidate staging
→ scaffold materialization
→ scorer design
→ scorer review
```

### 暴露的问题

生成的 scorer 真实出现：

```text
PATHS 中定义了 final_profile/resolution
后续代码却读取 arts["synthesis"]
```

最终导致：

```text
KeyError: 'synthesis'
```

同时，Scorer Review 正确发现了更深层的问题：

- scorer 主要依赖 keyword/presence heuristic；
- 没有正确重建 DAG；
- 没有 canonical runtime evidence reconciliation；
- 可能被 candidate-authored artifacts 自报刷分；
- 没有充分验证 assignment、acceptance、repair；
- evidence authority 和 temporal proof 不足。

### 解决方案

已经增加或确认了：

- scaffold validation；
- scorer design 先于 scorer implementation；
- scorer semantic review；
- scorer repair round；
- candidate status 和 validation evidence 持久化。

### 是否成功

**部分成功。**

成功发现了真实 scorer 缺陷，而不是把“代码能 import”误认为评分正确。

但 scorer repair 曾经长时间阻塞，真实 LLM repair 调用没有 bounded wall-clock control，无法把该环境标记为可发布。

---

## 10. Executor 输出负担问题

### 遇到的问题

原来的 Executor 一次返回：

```text
ExecutorResult
  └── BenchmarkItem
      └── ExecutableTaskContract
```

LLM 同时要输出：

- action；
- item metadata；
- source provenance；
- task contract；
- environment contract；
- coordination contract；
- artifacts；
- scoring；
- observations。

真实运行出现：

```text
Exceeded maximum output retries
```

### 解决方案

尝试将输出变为 contract-first：

```text
LLM 只返回 ExecutableTaskContract
Forge 本地包装 BenchmarkItem 和 ExecutorResult
```

并增加：

- PydanticAI timeout；
- bounded retries；
- 不再让模型填写可由系统确定的 wrapper 字段。

### 为什么这样做

这减少了结构化输出中的无关字段，保留原有角色语义，同时把生命周期和 provenance 留给 Forge。

### 是否成功

**部分成功，但不是最终主问题。**

在一次真实运行中，contract-first 能生成：

```text
executable_task
```

但后续仍可能因为环境契约不完整被 Verification 拒绝。

这证明：

```text
输出协议过重是问题之一
```

但不是唯一问题。

---

## 11. 当前真正的核心问题：Benchmark Assembly

### 事实

当前实验已经证明：

```text
Design Agent 能设计复杂任务
RAG 能提供复杂环境和评分先例
Executor 能生成 ExecutableTaskContract
```

但是：

```text
ExecutableTaskContract
→ 完整环境 bundle
```

仍然不稳定。

### 当前过重的实现

`materialize_environment()` 让单个 Agent 一次性返回：

```text
meta.yaml
core.py
scorer.py
tasks/*.json
materials/*
tests/*
README.md
```

然后另一个 Agent 审查整个 bundle，失败后再次要求 Agent 重写整个 bundle。

### 为什么这是错误的负担分配

环境文件不是一个单一输出，而是几个互相约束的组件：

```text
Manifest
Runtime
Scorer
Tests
```

如果一个文件修复导致整个 bundle 重写，会产生：

- 上下文过大；
- 文件内容相互漂移；
- scorer 和 task schema 不一致；
- core 和 tool entrypoint 不一致；
- tests 仍然针对旧实现；
- 任何局部错误都触发全量 repair。

### 正确解决方向

保持原五角色不变，只把 Executor 的 materialization 子流程拆成组件级步骤：

```text
ExecutableTaskContract
        │
        ├── Manifest Component Agent
        │      ├── meta.yaml
        │      ├── tasks/*.json
        │      └── README.md
        │
        ├── Runtime Component Agent
        │      ├── core.py
        │      └── mcp_server.py / tool entrypoint
        │
        ├── Scorer Component Agent
        │      ├── scorer.py
        │      └── scorer fixtures
        │
        └── Test Component Agent
               └── tests/test_environment.py
```

Forge 本地负责：

- 组件合并；
- 路径和环境 ID 校验；
- task/meta/scorer dimension 对齐；
- scorer signature 校验；
- AST/import smoke；
- pytest；
- 记录组件级 validation evidence。

Verification 只需要对失败组件提出 repair：

```text
manifest repair
runtime repair
scorer repair
test repair
```

而不是要求 Agent 重写整个 bundle。

### 是否成功

**尚未完成。**

这是下一阶段的主要开发任务。

目前已经确认了问题边界，但组件级 assembly 协议和局部 repair 还没有完成实现。

---

## 12. 曾经走错的方向及处理

### 错误方向

在一次调试中，曾尝试对 `existing_environment` 直接使用确定性适配器，绕过 LLM 生成环境契约。

### 为什么不符合系统目标

这会把系统从：

```text
Agent-only benchmark generation
```

改变成：

```text
已有环境 → 程序直接包装
```

它可以解决某些已有环境的字段完整性问题，但不能解决本项目真正要解决的：

```text
Agent 设计出的新 benchmark 如何被组装成完整环境
```

### 处理结果

该路径已撤回。当前方向恢复为：

```text
Agent 负责设计和生成
Forge 负责可靠组装、校验和控制
```

Forge 的确定性代码只负责：

- wrapper 字段；
- bundle 合并；
- schema/路径校验；
- 运行 smoke tests；
- 记录状态。

它不替代 Agent 进行 benchmark 设计。

---

## 13. 当前验收证据

### 已通过

```text
Benchmark Forge tests: 47 passed
Preference Registry tests: 14 passed
Preference Arena tests: 15 passed
```

真实 Preference Alignment：

```text
conflict evidence → abstain
adequate evidence → select_with_warnings
```

真实可执行任务生成：

```text
items = 1
item_kind = executable_task
```

### 尚未通过

```text
完整 generated environment bundle
scorer 语义 smoke
eval-system trial
agent-eval report
promotion readiness
```

### 当前不可声称的结论

不能把以下任一项当作完整成功：

```text
生成了 BenchmarkPlan
生成了 ExecutableTaskContract
生成了 scaffold 目录
scorer.py 能 import
```

完整成功必须是：

```text
bundle 完整
→ scaffold tests 通过
→ eval-system 执行成功
→ agent-eval 评分成功
→ evidence 可复核
```

---

## 14. 下一步实施顺序

下一阶段不继续扩大 Preference Arena，而是按以下顺序完成 Assembly：

### Step 1：冻结设计结果

不再改动已经验证的：

- Design prompt；
- Grounding 语义；
- Allocation 语义；
- BenchmarkPlanCandidate；
- ExecutableTaskContract。

### Step 2：定义组件级输出协议

建立：

```text
ManifestComponent
RuntimeComponent
ScorerComponent
TestComponent
```

每个组件只允许返回它负责的文件。

### Step 3：实现本地 BundleAssembler

Assembler 不使用 LLM，负责：

- 合并组件；
- 去重路径；
- 检查 environment_id；
- 检查 task/meta/scorer 一致性；
- 写入 staging registry。

### Step 4：每个组件独立 smoke

```text
manifest validation
runtime import
scorer import/signature
pytest tests/
```

任何组件失败，只进入对应组件的 repair。

### Step 5：增加 bounded repair

每个组件单独设置：

```text
max_repair_rounds
per-call timeout
wall-clock deadline
repair_failed typed status
```

不能让一次 scorer repair 无限阻塞整个 generation run。

### Step 6：再进入 eval-system/agent-eval

只有 bundle 本地验证通过以后，才运行：

```text
eval-system
agent-eval
```

Preference Alignment 继续作为可选的 materialization 前 gate，但不再作为当前主阻塞问题。

---

## 15. 最终总结

当前系统已经解决了：

```text
自然语言目标如何变成复杂开放 benchmark 设计
RAG 如何提供安全的环境先例
Agent 如何输出 ExecutableTaskContract
Preference 如何离线对齐
```

当前尚未解决的是：

```text
如何把一个复杂 ExecutableTaskContract 稳定组装成
meta.yaml + core.py + scorer.py + tasks + materials + tests
组成的完整可运行 benchmark
```

因此后续开发重点应明确为：

```text
不是继续增加更多设计 Agent
不是继续增加更多 Preference 流程
而是降低 Environment Assembly Agent 的单次负担，
实现组件级生成、组件级验证和组件级修复。
```

## 16. Environment IR 第一阶段实现

在确认“高层 Contract 到 bundle 之间缺少可实现规格”后，开始实现 Environment IR。第一阶段没有改变五个核心角色，也没有用确定性适配器替代 Agent，而是增加实现层中间表示。

已实现：

- `EnvironmentIR` 及 tool/state/artifact/evidence/rubric/task/component models；
- `lower_contract_to_ir()`；
- IR identifier 和 reference validation；
- `freeze()`，要求 IR 在组件 link 前冻结；
- `IRComponentOutput` 和 component ownership；
- component path collision 检查；
- component output link 成 `EnvironmentScaffoldBundle`；
- staging candidate 持久化 `environment_ir`。

验证结果：

```text
Benchmark Forge tests: 50 passed
```

这证明 IR 的基础模型、lowering 和 linker contract 已能单元测试，但还没有证明 LLM 组件 codegen 和最终 eval-system 运行已经完成。

下一步仍然是把现有 `materialize_environment()` 接到 IR pipeline：

```text
Contract → frozen IR → component Agents → linker → local validation → repair
```


## 17. 增加 Environment IR Compiler Agent

仅靠手写 `Contract → IR` lowering 不足以处理 Contract 中的自然语言语义。新增 `EnvironmentIRCompilerAgent`，让 Agent 根据 Contract 和 RAG 公开先例补全 tool、state、artifact、evidence、rubric 和 task binding。

编译输出先进入 `EnvironmentIRDraft`，再由 Forge 执行 cross-reference validation 和 freeze。验证失败时，将确定性错误作为反馈进行 bounded rewrite；超过上限则产生 typed `IRCompilationError`，不进入组件 codegen。

这保留了 Agent-only 的语义生成，同时避免让组件 Agent 各自猜 artifact/tool 名称。当前 role 已接入 `BenchmarkGenerationService` 的 candidate staging，冻结 IR 会保存到 `validation/environment-ir.json`。

当前状态：

```text
IR Compiler Agent：已实现
EnvironmentIRDraft：已实现
bounded rewrite：已实现
IR freeze/link：已实现
所有 component Agent 接入：尚未完成
```


## 18. IR 可演化性与表达能力失败

IR Compiler Agent 引入后，新增了一个重要区分：

```text
fillable hole
≠
IR expressiveness gap
```

如果 IR 已经有对应 construct，只是 binding 尚未确定，例如：

```text
artifact producer
state consumer
tool output field
rubric evidence binding
```

则允许 Compiler Agent bounded rewrite。

如果当前 IR 根本没有对应 construct，例如：

```text
coordination_graph
fault_model
session_registry
resource_model
human_interaction
```

则产生 `IRExpressivenessError`，不进行重写，不生成组件，也不把 benchmark 简化为当前 IR 能表达的形式。candidate 记录为：

```text
requires_ir_extension
```

当前已经实现：

- `CORE_IR_VERSION`；
- `CORE_IR_FEATURES`；
- `EnvironmentIR.ir_version`；
- `EnvironmentIR.required_features`；
- Contract expressiveness analysis；
- `IRExpressivenessError`；
- `requires_ir_extension` candidate status；
- expressiveness failure 的 staging 记录；
- 未知 feature 禁止通过 arbitrary extension fields 混入 IR；
- hole filling 和 expressiveness gap 的独立测试。

测试结果：

```text
Benchmark Forge tests: 53 passed
```

这一步解决的是“不要让 Compiler Agent 通过降级或自由 JSON 掩盖 IR 不足”，但 `coordination_graph` 等具体扩展 construct 仍然需要在真实 benchmark 反复出现并形成稳定语义后再添加。


## 19. 第一个 Typed IR Extension：CoordinationGraph

由于当前目标包含 subagent delegation，`coordination` 不是 speculative 未来字段，而是当前真实 benchmark 所需的稳定语义。因此实现了第一个 typed extension：

```text
coordination_graph → IR 1.1
```

对应类型：

```text
IRCoordinationGraph
IRCoordinationNode
```

它承载 subtask DAG、依赖、上下文、output contract、acceptance checks、write scope、parallel/assignment 约束和 repair budget。

这样基本的 subagent coordination Contract 不再被误判为 expressiveness gap，而是可以编译为 IR 1.1；`fault_model`、`session_registry`、`resource_model` 等尚未实现的语义仍然会触发 `IRExpressivenessError`。

当前测试：

```text
Benchmark Forge tests: 54 passed
```

## 近期发现：统一 EvidencePackage 与 LLM Rubric Evaluator

### 遇到的问题

要求 Scorer Agent 为每个开放式 benchmark 编写复杂验收程序，导致评分逻辑成为整个组装链路的最大不稳定源。即使 Contract、IR、Runtime 和 linker 正常，生成的 scorer 仍可能读取错误字段、把 self-report 当运行事实，或无法定位 eval-system 的 attempt workspace。

### 解决方案

把评分拆成两个稳定边界：

```text
eval-system / runtime
  → 事实
Evidence normalizer
  → 统一 EvidencePackage + deterministic checks
Frozen IR rubric
  → LLM Rubric Evaluator
```

同时保留 `scorer.py` 作为兼容入口，但其目标降为 Evidence Adapter，不再要求它实现 benchmark-specific 状态机。

### 本轮具体改动

1. IR rubric 保留 Contract 的 weight、minimum_score 和 critical_gate。
2. EvidencePackage 增加 `evidence_bindings`，解决 `evidence_artifact` 与 `artifact:final_report` 等逻辑 ID/具体 ID 不一致的问题。
3. 将 EvidencePackage 保持为跨模块的中立协议；不在 Benchmark Forge 内直接导入或调用 eval-system。
4. 增加 `validate_rubric_evaluation()`，重算加权分数并校验 threshold、critical gate、证据引用和 checksum。
5. 增加单元测试覆盖 binding、加权评分、防越权引用和 EvalSample 适配。

### 是否成功

**代码层成功。** benchmark-forge 测试从 59 个扩展为 62 个，全部通过。统一评分边界已经可以独立于具体 benchmark scorer 进行测试。

**真实运行层尚未完全完成。** 当前还需要由独立的离线消费方选择一批已有 `eval-system` trial，将其转换为 EvidencePackage，再调用真实模型的 `LLMRubricEvaluator` 并写入 agent-eval 报告。这是接入工作，不应通过放宽 schema 或增加 LLM 自由度来解决。

## 平级模块边界修订

### 新发现

将 `eval-system` 的 `EvalSample` 适配器放进 Benchmark Forge 会让生成器反向依赖执行系统，违反模块平级和产物协作原则。Benchmark Forge 的职责在生成 bundle 时已经结束，不应主动读取 attempt 或调用 agent-eval。

### 修正

已删除 Forge 内的 `normalize_eval_sample()` 入口，并保留中立的 `EvidencePackage` 数据结构。实际的运行产物适配由 agent-eval 一侧实现。

同时增加生成阶段的 `RubricIntegrityReview`。它不追求 rubric 的程序化细节，只检查目标漂移、范围过大/过小、方向反转和证据可观察性。

### 结果

代码测试为：

```text
62 passed
```

当前生成链路和离线评分链路已明确分开：

```text
Forge 产出 Benchmark Bundle
→ eval-system 执行并产出 attempt
→ agent-eval 消费 attempt 并评分
```

## 2026-08-26 真实稳定性检查：组件物化阶段

### 第一次真实运行发现的问题

真实运行目录：

```text
run/stability-check-20260826-escalated/
```

在 `instruction_following` 生成中，前五个角色和 IR Compiler 均完成，但流程在旧的 `scorer_design` 调用上长时间消耗预算，最终没有进入组件组装。原因是 `BenchmarkGenerationService` 即使已经切换到 Rubric 路径，仍然无条件调用了旧 `design_scorer()`。

### 修复

增加：

```python
enable_legacy_scorer_design: bool = False
```

默认关闭旧 scorer design；只有旧兼容流程显式打开时才调用。这样默认路径为：

```text
Contract → IR → Manifest/Runtime/Rubric/Tests → Linker
```

### 第二次真实运行结果

真实运行目录：

```text
run/stability-check-fixed2-20260826/
```

结果：

```text
Design             completed
Grounding          completed
Allocation         completed
Executor           completed
Verification       completed
IR Compiler        completed
Manifest Component completed
Runtime Component  completed
Rubric Component   completed
Tests Component    completed
Linker             passed
Static Validation  passed
```

耗时约 398 秒，生成 1 个 scaffold，候选状态为 `scaffolded`。旧 `scorer_design` 不再出现在 telemetry 中，说明修复已生效。

### 第二次真实运行继续暴露的问题

对生成出的 bundle 直接运行其 tests 时，发现两个真实组装错误：

1. `meta.yaml` 使用了 `python -m constraint_review_env.mcp_server`，但平面 bundle 的 ownership 只有根目录 `mcp_server.py`，并不存在 `constraint_review_env/` 包。
2. `tests/test_artifacts_contract.py` 在干净环境启动时直接断言运行期 artifact 已存在；但这些 artifact 应由被评测 Agent 在 attempt 中生成，不能成为 bundle 初始化测试的前置条件。

原来的 `validate_scaffold()` 没有检查这两类错误，因此把不真正可运行的 bundle 标记为 valid。这是一个实际的静态验证缺口。

### 修复

已完成：

- `normalize_octagon_scaffold()` 将无对应 package 的平面 MCP 入口规范化为 `mcp_server`；并修正测试中的 package-style import。
- `validate_scaffold()` 增加入口模块 ownership 检查。
- `validate_scaffold()` 拒绝在干净启动阶段要求 `artifacts/*.is_file()` 的测试。
- Tests Component Prompt 明确：测试不得依赖运行期 artifact，必须使用临时 fixture；不得发明不存在的 Python package。

对旧真实生成 bundle 重新执行静态检查后，验证器现在正确报告：

```text
valid = false
entrypoint references missing package module
requires pre-existing runtime artifacts
```

这说明修复后的验证器能够抓住之前漏掉的错误，而不是继续把错误 bundle 当成成功。

### 当前结论

本轮真实检查确认：

```text
旧 scorer-design 无条件调用问题       已解决
组件生成与 linker 主链路              已通过一次真实运行
flat MCP entrypoint 不一致             已由 normalizer/validator 修复
测试错误依赖运行期 artifact            已由 prompt/validator 修复
```

仍需再用新的生成结果完成一次完整的 bundle smoke run，确认模型生成的 Runtime、MCP、Tests 在规范化后都能实际运行。这个步骤属于下一轮真实实验，不应通过重新打开旧 scorer design 来解决。

## 2026-08-26 第二个能力重放：IR 修复与测试契约问题

### 真实结果

使用 `robustness_fault_tolerance` 的已有 Benchmark Contract 重放：

```text
run/stability-replay-robustness-20260826/
```

IR Compiler 之前因为模型错误输出：

```text
required_features: [ ..., coordination_graph]
coordination: null
```

而 Contract 实际没有 CoordinationContract，导致：

```text
required feature coordination_graph has no typed construct
```

### 修复

加强 IR Compiler Prompt，明确：

```text
只有 Contract 存在 CoordinationContract 且 draft 有 typed coordination
时才能声明 coordination_graph。
Fault injection、retry、resource limit、tool trace 都不等于 coordination_graph。
```

使用同一个 Contract 重放后：

```text
IR compiled and frozen
Manifest generated
Runtime generated
Rubric generated
Tests generated
Linker succeeded
```

说明该 IR 边界修复在真实重放中生效。

### 继续发现的问题

生成 bundle 的测试中有一条：

```python
assert "artifacts/sync_report.json" in source
```

它检查的是某个实现文件是否包含特定字符串，而不是检查公开运行行为。不同合法实现可能把路径放在 `core.py` 常量中，因此这属于脆弱的测试契约，并非真实 Benchmark 要求。

同时，旧验证器中的 artifact 检查规则过宽，曾把合法的：

```python
Path("mcp_server.py").is_file()
```

误判成运行期 artifact 前置依赖，因为同一个文件中还出现了 `artifacts/` 字符串。

### 修复

- Tests Component Prompt 明确只检查公开行为或公开常量，不检查实现源代码中的字符串。
- artifact 前置检查收窄为只匹配 `Path("artifacts/...").is_file()`。
- 新增静态检查，拒绝通过 `read_text()` 后断言实现源码字符串的测试。

修复后：

```text
Benchmark Forge tests: 64 passed
core.py/mcp_server.py/scorer.py AST + py_compile: passed
```

旧 replay 报告中的 `static_validation.valid=false` 是修复前生成的历史结果；重新使用修复后的验证器检查时，前置 artifact 的误报已经消失。该 bundle 的 source-text 测试仍会被新的验证器拒绝，直到通过 Tests Component 重新生成。

## 2026-08-26 Tests Component 定向重放

为了不重新生成已经正确的 Manifest、Runtime 和 Rubric，使用同一个：

```text
robustness_fault_tolerance Contract
Frozen EnvironmentIR
```

只重放 Tests Component，再通过 linker 重新组装。这符合固定 Contract 重试边界，不重新执行 Design/Grounding/Allocation，也不修改其他组件。

首次定向重放脚本自身漏传 `tasks/*.json`，被 linker 正确拒绝：

```text
scaffold requires at least one tasks/*.json
```

修正重放脚本后再次运行：

```text
Tests Component generated
Linker passed
Static Validation passed
```

输出目录：

```text
run/stability-replay-robustness-20260826-tests-fixed/
```

随后在隔离 bundle 根目录执行生成测试：

```text
8 passed in 0.04s
```

这确认新的 Tests Component Prompt 已经避免了之前的两个问题：

```text
不再依赖预先存在的 runtime artifact
不再使用错误的 package-style MCP import
不再通过检查实现源码字符串验证行为
```

## 2026-08-26 delegation_quality 真实生成与定向修复

真实初次生成完成了 Contract，但 IRDraft 将 `task_binding` 作为 runtime state consumer，违反引用协议。加强 Compiler Prompt 后，同一 Contract 重放成功完成 IR、四组件、linker 和 static validation。

重放 bundle tests 为 `10 passed`，但 static validation 给出 scorer 引用隐藏 coordination node IDs 的 warning。检查发现 scorer 把 IR 中的固定 DAG 当作唯一正确拆解，而公开任务要求被评测 Agent 自己设计合理拆解。这会惩罚语义等价的方案，是评分检查项偏移。

将该问题升级为静态 error，并修改 Scorer Component Prompt：除非公开 prompt 明确命名，否则不得要求固定 node ID、固定节点数或唯一 DAG，只能根据 rubric 性质评价。仅重放 Scorer Component 后：

```text
static validation: valid
bundle tests: 10 passed
hidden fixed node IDs: absent
```

随后运行 Rubric Integrity Review，结果：

```text
verdict: pass
target_alignment: aligned
confidence: 0.97
```

Review 已接入主生成 service。`reject` 会阻止 component codegen；`revise` 在当前 MVP 放宽门槛下记录 warning 并允许继续。


## 2026-08-26 context_compression_fidelity：Rubric 与 Tests 两级定向修复

### 初次真实生成

第四个能力样本使用 `context_compression_fidelity`。Contract 和 IR 均首次成功，但 Rubric Integrity Review 返回 `revise`：

```text
provenance_fidelity 范围过严：
要求最小必要来源集合并惩罚冗余来源，超出公开任务目标。

state_recovery_and_completion 范围过窄：
没有明确检查 memory_snapshot 的必备结构、version、压缩预算和摘要统计。
```

保持 criterion IDs、数量、权重、阈值和其他 IR registry 不变，只修改上述两个 criterion description。新 Frozen IR checksum 为：

```text
sha256:355066d41858f9f345aa897f67451a5b852776be6c325e0447b1b1bbcb21e0cf
```

第二次 Rubric Integrity Review：

```text
verdict: pass
target_alignment: aligned
confidence: 0.96
```

这验证了 rubric-only bounded revision：它能修正检查范围，而不重做 Contract、IR registry 或组件架构。

### Metadata normalization 问题

首次用 revised IR 组装时，`meta.yaml` 仍缺少 protocol、task_id、tools、artifacts，并保留旧 Contract rubric 描述。原因不是 Manifest Agent 不会生成，而是 deterministic normalizer 没有接收 Frozen IR，导致链接后公共 metadata 丢失或回退到旧 Contract。

修复为：

```python
normalize_octagon_scaffold(bundle, item, ir)
```

由 IR 规范化公共 tool/artifact/rubric metadata。这样 bounded rubric revision 不会在 materialization 阶段被旧描述覆盖。

### Tests Component 第一次定向重放

只重放 Tests，复用 Manifest、Runtime、Scorer 和 revised Frozen IR。结果：

```text
Tests generation: 61.333s
Linker: passed
Static Validation: valid
```

新测试已经做到：

```text
不使用 inspect.getsource
不读取实现源码断言 tool IDs/scorer refs
不要求运行前 artifact 已存在
```

但真实执行 bundle tests 得到：

```text
2 failed, 9 passed, 2 skipped
```

失败原因是 Tests Agent 猜测 scorer 接口，依次尝试位置参数、workspace 和 artifacts_dir，而 Forge 已规定公共 keyword-only 接口。这个问题 static validator 无法仅从结构上可靠发现，必须通过真实 bundle tests 暴露。

### Tests Component bounded repair

将真实失败反馈给 Tests Agent，明确：

```python
score(
    attempt_id=...,
    task=...,
    env_db=None,
    trace=None,
    final_state=...,
)
```

只允许修复 Tests Component，不修改 scorer 来迁就错误测试，也不重跑 Design/Grounding/Allocation/Executor/Verification/IR Compiler。

最终：

```text
repair generation: 49.137s
linker: passed
static validation: valid
bundle tests: 11 passed, 2 skipped
project regression: 68 passed
Python compile: passed
```

最终 bundle 位于：

```text
run/stability-replay-context-compression-rubric-20260826-tests-repaired/bundle/
```

### 网络权限诊断

定向重放曾在 180 秒和 300 秒硬截止时失败。最初看似模型负担问题，但最小 API 检查证明 filesystem sandbox 禁止 socket；获准联网后相同调用正常完成。因此实验统计必须区分：

```text
transport/sandbox failure
provider/model timeout
structured-output failure
semantic validation failure
bundle runtime/test failure
```

否则基础设施问题会污染 Agent 稳定性结论。

### 当前结论

这个样本验证了分层修复策略：

```text
Rubric 偏移
→ rubric-only revision

公共 metadata 丢失
→ deterministic normalizer 修复

Tests 调错 scorer 接口
→ tests-only bounded repair
```

每次只修改拥有该职责的模块。不同组件和三个平级项目之间仍只通过明确产物/接口协作，没有让一个模块调用另一个模块的内部实现。

## 2026-08-26 被测对象从“有工具的 LLM”纠正为“完整 Agent”

工具审计发现 `delegation_quality` runtime 明确声明不执行 native agents，而是用内存对象和 synthetic status/output 模拟 child Agent。这说明此前虽然把题目从选择题改成开放工具任务，但 Design Agent 仍把被测对象理解成“会调用 benchmark 工具的 LLM”。

同类问题也出现在 `context_compression_fidelity`：环境手写 `memory_write/memory_read` 后，任务测到的是模型是否会将摘要写入外部存储，而不是 host Agent 自身发生 context compaction/reset 后能否恢复。

修复不应只是继续增加工具，而是先冻结被测对象：

```text
完整 Agent = LLM + host runtime + native tools + workspace + memory + subagents
```

环境只负责外部任务世界、受控数据/故障/状态和独立验证。新增 typed tool ownership，并加强四个角色 prompt 与 static validator，拒绝环境重实现 subagent 生命周期或用 synthetic memory 代替 native context management。

## 2026-08-26 native delegation 重新生成验证

修正角色 prompt 后，Design 和 Grounding 已能明确区分完整 Agent 与环境。新的 Executor Contract 要求 host Agent 原生 subagent lifecycle，并把 case/fault/verifier 分开归属。实验进一步暴露三点：

1. 极小 typed binding 修改不应让 Agent 重写整个 Contract；ownership 单字段 repair Agent 反而产生 schema 漂移。
2. 当 decomposition 是被测能力时，不应预写隐藏 Coordination DAG；应评价真实轨迹的覆盖、依赖、并行、验收和整合性质。
3. IR 中虽然声明 `tests depends_on runtime/scorer`，旧实现却没有把依赖输出传给 Tests Agent，导致它猜测 Runtime fixture 和 scorer interface。现在 component generation 会按 `depends_on` 传递已有组件接口上下文。

最终新 delegation bundle 不实现任何 native subagent 工具，公开 MCP 只包含受控 case reader 和 artifact registry，clean tests 为 `4 passed`。

## 2026-08-26 从“Codex 编排修复”升级为自动 Materialization Workflow

### 问题

Contract、IR Compiler、四个 Component Agent、linker 和 static validator 都已经存在，但它们仍是分散能力。真实失败后仍需要 Codex：

```text
读取 pytest/report
→ 判断 Tests/Runtime/Scorer/Manifest 谁负责
→ 写一次性 replay
→ 只重放某个组件
→ 再 link/test
```

这使“系统能生成”与“系统能独立完成生成”成为两件事。

### 实现

新增：

```text
src/benchmark_forge/materialization_workflow.py
scripts/replay_contract_autonomously.py
scripts/summarize_materialization_runs.py
docs/AUTONOMOUS_MATERIALIZATION.md
```

核心约束：

```text
Accepted Contract 不变
Frozen IR checksum 不变
优先机械诊断
歧义时调用 bounded Diagnosis Agent
只修责任 component
自动 relink / static validate / optional pytest
成功 IR 和组件按 Contract+IR checksum 恢复
```

`BenchmarkGenerationService` 的 Frozen IR component 路径现已默认接入自动 materialization workflow；不再只能一次生成四组件后停止。

### 真实实验

固定旧 `instruction_following` Contract 运行：

```text
IR compile                         成功
四组件首次生成                     成功
link/static validation             成功
bundle tests                       3 failed, 6 passed
Diagnosis Agent                    定位 Runtime + Tests
Runtime-only repair                成功
Tests-only repair                  成功
relink/static validation           成功
bundle tests                       9 passed
最终状态                           ready
人工/Codex 介入                    0
```

首次非缓存运行指标：

```text
model calls                        8
IR compile attempts                1
component generated                4
component repaired                 2
link attempts                      2
bundle test attempts               2
Agent diagnosis                    1
manual interventions               0
```

Diagnosis Agent 正确区分了两种缺陷：

1. `mcp_server.py` 使用 package-relative import，但公开入口是 flat `python -m mcp_server`，属于 Runtime；
2. Tests 要求 validator 抛异常，但 Runtime 的公开协议是返回结构化 invalid result，属于 Tests 的错误实现假设。

这正是此前需要 Codex 阅读 traceback 后才能完成的判断。

同一 Contract 第二次回放：

```text
IR reused                          1/1
components reused                  4/4
model calls                        0
bundle tests                       9 passed
```

### 是否解决

已解决：

- 明确 ownership/link/static 错误的自动责任路由；
- pytest 歧义的 bounded Agent 诊断；
- component-only repair；
- 固定 Contract/IR 的恢复式重放；
- 重复运行不再浪费模型调用；
- 可机器统计人工介入率。

尚未解决：

- 该实验只有一个真实 Contract，不能据此声称总体零人工率；
- 本地 pytest 不是生产隔离，正式执行仍由平级 eval-system 消费 bundle；
- Contract construct validity、IR schema evolution、pilot 难度/区分度仍需要离线治理，不应交给 component repair 自动决定。

## 2026-08-27 角色职责审计：工程可运行不等于场景有效

自动 materialization workflow 将旧 `instruction_following` Bundle 修复到 `9 passed` 后，进一步检查发现：

```text
Contract environment.materials = []
Bundle 没有 records.json / materials/
Runtime 缺少 fixture 时返回 []
Tests 只使用 test-only fixture
Scorer 对空 inventory 可返回 100 分
```

这说明前一阶段成功解决的是组件工程组装和 Codex 定向修复介入问题，尚未解决任务资料与场景有效性。

责任链路审计结论：

```text
Design 高层构造基本正确，但未量化最低场景复杂度
Grounding 未将理论可生成与当前可消费资源严格区分
Allocation 未区分 Contract quota 与 materialization quota
Executor Contract 声称有 synthetic fixture，但没有 Material binding
Verification 没有把必需资料缺失作为阻断错误
EnvironmentIR 缺少 Material Registry / Scenario Model
Runtime 将缺失资料静默降级为空场景
Tests 使用 test-only fixture 掩盖 production fixture 缺失
Scorer 将 invalid environment 错误解释为 Agent 满分
```

完整审计与验收标准见：

```text
docs/ROLE_RESPONSIBILITY_GAPS.md
```

当前案例状态修正为：

```text
engineering_valid = true
scenario_complete = false
pilot_ready = false
```

后续应修复系统接口和门禁，而不是只为当前案例手工补一份 `records.json`：

```text
MaterialContract
→ IR Material/Scenario typed extension
→ Verification blocker
→ Scenario Completeness Validator
→ Runtime fail-closed
→ Scorer invalid_environment
→ production fixture / discrimination tests
```

## 2026-08-27 Material/Scenario typed gate 落地

根据 `ROLE_RESPONSIBILITY_GAPS.md`，实现第一轮系统级修复：

```text
MaterialContract 扩展
ScenarioContract
IRMaterial
IRScenario
material_registry / scenario_model（IR 1.2）
scenario_contract pre-codegen gate
scenario_completeness scaffold gate
Runtime fail-closed prompt contract
Scorer invalid_environment prompt/static contract
Tests production fixture / calibration contract
```

Manifest ownership 扩展为：

```text
meta.yaml
README.md
tasks/
materials/
schemas/
```

同时增加 `validate_ir_contract_bindings()`，确保 IR Compiler Agent 不会丢失或削弱 Contract 的材料和场景约束。

旧 `instruction-following-constraints-001` Contract 重新通过固定回放入口后，结果为：

```text
status                    scenario_incomplete
reason                    generated data-dependent task has no MaterialContract
                          or typed scenario generator/injection binding
model_calls               0
IR compile attempts       0
component generation      0
manual intervention       0
```

证据：

```text
run/scenario-gate-replay-20260827/report.json
run/scenario-gate-fixed-replay-20260827/workflow-report.json
```

这证明失败已经从“生成完空 Bundle 后由人发现”前移到“Contract 验证后立即阻断”。

另增加大材料 dependency prompt 摘要：超过 2KB 的 `materials/*` 不再被完整复制给每个后续 Component Agent，只传 byte count、SHA-256 和公开消费说明，避免重新引入 prompt 爆炸。

## 2026-08-27 Artifact Schema 共享绑定与固定 Contract 真实重放

### 发现

Material/Scenario 修复后的首个真实 Bundle 已有 production material，但最终 Artifact 协议仍断裂：

```text
fixture.output_schema_ref 指向 fixture schema
schemas/constraint_task_fixture.schema.json 验证的是 fixture 本身
IRArtifact.schema_def 只有 {type: object}
Runtime 找不到内嵌 output_schema 时只验证 dict
Scorer 猜测 cases/results/responses/answers/items/outputs
公开 task 没有明确 final_output 内部字段
```

因此旧 Bundle 的 `validate_output` 名义上验证 schema，实际上没有验证最终产物结构。

### 第一轮修复：IR 1.3 artifact_schema

实现：

```text
ArtifactRequirement.schema_path / schema_def
IRArtifact.schema_path
required_feature=artifact_schema
IR Compiler bounded hole filling
Contract↔IR artifact binding validation
Manifest/Runtime/Scorer/Tests prompt contract
IR/meta/schema file/public task static consistency check
```

回归测试从：

```text
92 passed
```

增加到：

```text
96 passed
```

固定原 Contract 第一次真实重放：

```text
status                    ready
IR version                1.3
model calls               5
IR compile attempts       1
components generated      4
component repairs         0
static validation         passed
manual interventions      0
```

证据：

```text
run/artifact-schema-replay-20260827-network/
```

但审计 schema 后发现：

```json
{"type":"object","minProperties":1,"additionalProperties":true}
```

这证明“组件共享同一个 schema”已解决，但“schema 是否表达 Contract 的评分覆盖语义”尚未解决。

### 第二轮修复：schema semantic sufficiency gate

增加机械规则：

```text
named required fields
禁止任意顶层字段（当 Contract 明确禁止 unrequested fields）
all-case coverage → required per-case array
minItems >= scenario.minimum_items
case item requires identity + response
```

使用同一个 Contract 再次重放：

```text
status                    ready
IR compile attempts       1
model calls               5
component repairs         0
link/static validation    passed
manual interventions      0
```

新的 canonical schema：

```text
normal branch:
  cases: array(minItems=6)
  item required: case_id, response
  additionalProperties=false

safe-degradation branch:
  required: clarification
  optional: safe_degradation
  additionalProperties=false
```

证据：

```text
run/artifact-schema-case-coverage-replay-20260827/
```

同一输出目录再次重放：

```text
model calls               0
IR reused                 1
components reused         4
link/static validation    passed
```

### 是否成功解决

已解决：

```text
final artifact schema 不再由各组件猜测
fixture schema 与 output schema 明确分离
公开 Agent 能看到 canonical schema path
IR Compiler 的宽松 schema 会被 bounded rewrite 拒绝
同一 Contract 的成功产物可零模型调用恢复
```

仍未完成：

```text
新 Bundle 的生成代码尚未在隔离 eval-system 动态执行
尚未正式运行被测 Agent
最终正确率、难度、区分度仍无 pilot 数据
```

因此当前状态是：

```text
Contract/IR/component engineering candidate = ready
isolated dynamic validation                 = pending
formal Agent pilot                           = pending
promotion                                    = not yet allowed
```

## 2026-08-27 真实 AgentOctagon 运行暴露 Runtime ABI 不兼容

### 真实失败

`artifact-schema-case-coverage-replay-20260827/bundle` 首次进入 AgentOctagon 后，三个 Agent 都没有完成有效任务。根因不是 instruction-following 难度，而是 Bundle 与当前 runtime ABI 不一致：

```text
materials 写成 typed list，loader 需要 materials.agent mapping
material 没有本地 path
schema 没有 workspace mount
core 普通函数没有 @env_tool 注册
tool_count=0
entrypoints 没有有效 MCP 注入
Agent 直接请求内部 endpoint 得到 401
scorer 在 timeout/缺失数据路径抛异常
```

因此该次 Claude/Codex 超时和 scorer exception 被判定为：

```text
infrastructure-invalid
not comparable
not usable for ranking
```

### 系统级修复

新增：

```text
src/benchmark_forge/agent_octagon_abi.py
docs/AGENT_OCTAGON_RUNTIME_ABI.md
tests/test_agent_octagon_abi.py
```

Normalizer 现在输出：

```text
materials.agent path/target mounts
material_contracts typed registry
entrypoints.mcp authenticated stdio bridge
runtime_abi=agent-octagon.env-loader.v1
准确 prerequisites
```

Runtime Component 现在必须：

```text
core.py 使用 @octagon.env_api.env_tool
mcp_server.py 使用 FastMCP
读取 OCTAGON_ATTEMPT_ID/OCTAGON_ENV_TOKEN/OCTAGON_BASE_URL
代理 attempt tool endpoint
```

Scorer Component 现在必须：

```text
env_db Path → env_db.parent/skill_workspace
trace list → row.tool_name/result
所有失败路径返回 numeric values
不返回 value=None
不对 Optional 直接 int/float
```

### 固定 Contract/Frozen IR 定向修复

没有重跑 Design/Grounding/Allocation/Executor Contract，也没有重编译 IR。

第一轮 ABI lint 自动发现并修复：

```text
Runtime
Scorer
```

指标：

```text
model_calls               2
reused_ir                 1
reused_components         4
repaired_components       2
automatic_diagnoses       1
manual_interventions      0
```

第二轮新门禁继续发现 scorer 误解 `env_db/trace` ABI，只修 Scorer：

```text
model_calls               1
reused_ir                 1
repaired_components       1
manual_interventions      0
```

最终：

```text
link/static validation    passed
AgentOctagon ABI lint      passed
Forge regression tests    100 passed
```

可信 integration fixture 还通过 AgentOctagon 自身运行时实际验证：

```text
EnvLoader
material copying
MCP spec parsing
tool_count=1
RegisteredTool.call
```

真实生成 Bundle 的下一次 Agent run 尚未执行，因此当前只能标记：

```text
static/runtime-ABI candidate = ready
isolated generated-bundle dispatch = pending
formal benchmark result = pending
```

## 2026-08-27 run_c59027905ee4 人工复核后的语义与公平性修复

### 人工复核发现

官方分数 `Codex 92 / Claude Code 72 / Blade Agent 64` 数学上可复算，但不能作为能力排名。主要原因：

```text
case-004 没有执行 priority/precedence lowering
18 条原始约束中只有约 8 条字符串约束进入计分
unsupported constraint type 被静默 return None / continue
intent_coverage 只统计 case_id
unjustified_deviation 重复惩罚普通 constraint failure
MCP command 假定 envs/<environment-id> 安装布局
跨 attempt 可见性与凭证日志破坏公平性
```

该 run 已记录为：

```text
mathematically_reproducible = true
semantic_score_valid        = false
fair_comparison_valid       = false
classification              = infrastructure_and_scorer_invalid
```

记录文件：

```text
run/artifact-schema-case-coverage-replay-20260827/RUN_C59027905EE4_REVIEW.json
```

### Scorer 修复

保持 Contract 与 Frozen IR checksum 不变，只修 Scorer/Tests checkpoint：

```text
SUPPORTED_CONSTRAINT_TYPES 显式声明
fixture constraint types 与 scorer evaluator 机械 link
unknown type → invalid_environment
precedence case → _effective_constraints lowering
低优先级冲突 claim 不进入 denominator
format/audience/tone/count/topic/distractor/late_injected 全部有 evaluator
intent_coverage 检查实质任务意图
format_validity detail 分离 schema 与 validator evidence
unjustified_deviation 不再重复扣普通约束失败
```

校准新增：

```text
每种公开 constraint type 至少一个 negative case
case-004 正确高优先级回答必须通过
包含被覆盖 blocked claim 必须失败
unsupported type 必须 invalid_environment
缺 validator 时 schema 与 invocation 子证据可区分
普通 constraint failure 不重复降低 unjustified_deviation
```

结果：

```text
Bundle tests: 19 passed
Forge tests:  103 passed
```

### MCP 可迁移部署修复

原组合：

```text
Forge command = envs/<id>/mcp_server.py
AgentOctagon cwd = env.env_dir.parent.parent
```

只对仓库内 `envs/` 布局有效，对独立 bundle 无效。

新 ABI：

```text
meta command = [python, mcp_server.py]
AgentOctagon python token → runtime sys.executable
AgentOctagon cwd          → env.env_dir
```

真实 MCP client smoke test 已完成：

```text
initialize → protocolVersion 2025-11-25
tools/list → [validate_output]
```

这证明 `tool_count=1` 与 Agent 实际 MCP 可见性现在同时成立，而不只是 EnvLoader 后端注册成功。

### 固定重放

最终固定 Contract/Frozen IR 重放：

```text
run_id              mat-20260827T053903Z-315c152b
status              ready
model_calls         0
reused_ir           1
reused_components   4
static validation   passed
bundle tests        19 passed
manual intervention 0
```

仍未由 Bundle 解决的问题属于 AgentOctagon/eval-system 运行治理边界：

```text
跨 attempt 文件隔离
宿主源码/数据库可见性
attempt token 日志脱敏
infrastructure failure 的正式聚合与 run invalidation
```
