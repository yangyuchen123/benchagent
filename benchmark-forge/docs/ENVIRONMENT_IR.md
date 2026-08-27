# Environment IR：从 Benchmark Contract 到可组装环境

> 状态：设计冻结前的实现说明
>
> 核心结论：`ExecutableTaskContract` 描述 benchmark 的高层语义；`Environment IR` 描述各环境组件之间必须共享的可实现连接关系。

## 1. 为什么需要 IR

当前高层对象适合回答：

```text
要评测什么能力？
任务大致需要什么环境？
应该观察哪些行为？
应该如何评分？
```

它还不足以回答：

```text
artifact 的规范名称是什么？
tool 的参数和返回字段是什么？
哪个 runtime state 是 canonical source？
scorer 读取哪个 evidence？
task、runtime、scorer、tests 是否引用同一字段？
```

如果直接让多个 Agent 从高层 Contract 各自生成组件，会出现：

```text
Runtime Agent：final_result
Scorer Agent：synthesis
Test Agent：result
Manifest Agent：output
```

每个局部输出都可能合理，但 bundle 无法连接。

因此流程必须变成：

```text
Benchmark Contract
      ↓
Environment IR
      ↓
component agents
      ↓
link / consistency checking
      ↓
bundle
```

这和编译器的：

```text
高层语义 → 中间表示 → 分模块 codegen → link → validation
```

是同一种问题结构。

## 2. Contract 与 IR 的边界

### Benchmark Contract

`ExecutableTaskContract` 负责表达：

- benchmark 的能力目标；
- 公开任务指令；
- 任务约束；
- 需要的环境类型；
- 需要观察的行为；
- 产物和评分意图；
- 难度和扰动方向。

它是面向 benchmark 设计者的规格。

### Environment IR

`Environment IR` 负责表达：

- canonical tool registry；
- tool input/output schema；
- runtime state schema；
- artifact registry；
- evidence authority；
- rubric/evidence binding；
- task references；
- workspace 和权限边界；
- component dependency graph；
- bundle 文件和符号的连接关系。

它是面向环境实现、scorer、tests 和 assembler 的规格。

IR 不改变 benchmark 的目标，也不重新设计能力维度；它把高层目标编译成可实现的连接协议。

## 3. Environment IR 的最小结构

建议的公开 IR 对象：

```text
EnvironmentIR
├── schema_version
├── environment_id
├── task_id
├── protocol
├── tools: ToolIR[]
├── runtime_state: StateFieldIR[]
├── artifacts: ArtifactIR[]
├── evidence: EvidenceIR[]
├── rubric: RubricIR
├── task_binding: TaskBindingIR
├── workspace: WorkspaceIR
├── components: ComponentGraphIR
└── provenance
```

### 3.1 ToolIR

```yaml
name: fetch_quote
interface: mcp
input_schema:
  type: object
  required: [source]
  properties:
    source:
      type: string
      enum: [alpha, bravo, charlie, delta, echo]
output_schema:
  type: object
  required: [source, quote]
  properties:
    source: {type: string}
    quote: {type: number}
errors:
  - name: invalid_source
  - name: duplicate_call
trace_event: tool_call.fetch_quote
```

所有组件都引用 `tool_id`，不重复重新解释工具字段。

### 3.2 StateFieldIR

```yaml
state_id: merged_basis_price
path: runtime.state.basis_price
type: number
producer:
  tool_id: combine_price
  output_field: basis_price
consumers:
  - tool_id: fetch_detail
  - tool_id: finalize_report
authority: environment_state
```

这样可以明确：

```text
basis_price 由谁产生
谁可以读取
读取哪个字段
哪个证据源拥有最终权威
```

### 3.3 ArtifactIR

```yaml
artifact_id: dependency_plan
path: artifacts/dependency_plan.json
media_type: application/json
schema:
  type: object
  required: [nodes, edges, execution_order]
producer: agent_workspace
required: true
authority: workspace_artifact
temporal_constraint:
  must_exist_before: first_business_tool_call
```

Artifact 的 canonical identity 是 `artifact_id`，路径只是它的物理位置。

所有组件必须通过 `artifact_id` 引用它，而不是自行写字符串路径。

例如：

```text
Runtime → artifact_id=final_report
Scorer  → artifact_id=final_report
Tests   → artifact_id=final_report
```

由 IR linker 统一解析为：

```text
artifacts/final_report.json
```

### 3.4 EvidenceIR

```yaml
evidence_id: canonical_tool_trace
source_type: runtime_trace
authority: environment_runtime
schema_ref: trace.v1
read_interface: runner.trace.events
allowed_consumers:
  - rubric.scheduling_parallelism
  - rubric.dependency_fidelity
```

Evidence 必须声明：

- 来源；
- schema；
- 权威性；
- 可读取接口；
- 允许哪些 rubric 使用。

这样可以防止 scorer 把 Agent 自报的文本当成 canonical runtime evidence。

### 3.5 RubricIR

```yaml
rubric_id: dependency_fidelity
weight: 25
pass_threshold: 60
criteria:
  - criterion_id: all_fetches_before_merge
    evidence_refs:
      - canonical_tool_trace
      - runtime_state.merge_status
  - criterion_id: no_premature_derive
    evidence_refs:
      - canonical_tool_trace
implementation_constraints:
  deterministic: true
  self_report_allowed: false
```

Rubric 不能直接写任意路径或任意字符串；它只能引用 IR 中已注册的 evidence、state 和 artifact。

## 4. IR 的编译流程

### Step 1：Contract lowering

输入：

```text
ExecutableTaskContract
RAG precedents
provider profile
```

输出：

```text
EnvironmentIR draft
```

这个阶段可以使用 Agent，但 Agent 只能补充高层 Contract 中缺失的实现连接，不得重新发明 benchmark 目标。

### Step 2：IR normalization

Forge 本地执行：

- ID 规范化；
- artifact_id 唯一性检查；
- tool_id 唯一性检查；
- state producer/consumer 检查；
- evidence authority 检查；
- rubric 引用存在性检查；
- workspace 路径安全检查；
- component dependency 检查。

### Step 3：IR freeze

只有通过 normalization 的 IR 才能进入组件 Agent。

IR freeze 后：

- component Agent 不能修改 artifact canonical name；
- component Agent 不能修改 tool schema；
- scorer Agent 不能声明新的 evidence；
- test Agent 不能改变任务 contract；
- 修改 IR 必须重新执行所有下游生成和验证。

### Step 4：Component codegen

组件 Agent 分别接收：

```text
Manifest Agent ← EnvironmentIR 的 task/environment/material 部分
Runtime Agent  ← tools/state/workspace 部分
Scorer Agent   ← rubric/evidence/state/artifact 部分
Test Agent     ← 全部 IR，但只生成测试
```

每个 Agent 只返回自己负责的文件和实现 notes，不返回完整 bundle。

### Step 5：Link

Forge 本地执行：

```text
component outputs
  → path collision check
  → symbol/reference resolution
  → artifact path expansion
  → tool schema consistency
  → scorer evidence binding
  → task/meta alignment
  → final bundle
```

### Step 6：Validation

验证顺序：

```text
IR validation
→ schema validation
→ Python AST/import
→ component tests
→ scorer smoke
→ bundle tests
→ eval-system pilot
```

## 5. 组件 Agent 的职责边界

### Manifest Component Agent

只负责：

- `meta.yaml`；
- `tasks/*.json`；
- `README.md`；
- 公开任务描述和入口声明。

不得：

- 改变 tool schema；
- 改变 artifact canonical name；
- 定义 scorer 的 evidence 读取逻辑。

### Runtime Component Agent

只负责：

- `core.py`；
- `mcp_server.py` 或其他公开入口；
- 工具调用和 runtime state 的实现。

必须引用 IR 中已经冻结的：

```text
tool_id
input_schema
output_schema
state_id
artifact_id
```

不得：

- 自己新增 rubric；
- 自己改变 task 产物名称；
- 直接实现隐藏答案判断。

### Scorer Component Agent

只负责：

- `scorer.py`；
- scorer fixtures；
- 评分相关测试。

必须：

- 只读取 IR 声明的 evidence；
- 使用 IR 声明的 artifact/state/tool trace；
- 明确 evidence authority；
- 拒绝只依赖 Agent 自报的方案；
- 支持缺失 evidence 的确定性 fallback。

不得：

- 读取 private/expected；
- 重新猜 artifact 路径；
- 通过字符串搜索替代 canonical evidence；
- 修改 rubric。

### Test Component Agent

只负责：

- bundle contract tests；
- tool schema tests；
- artifact schema tests；
- scorer calibration tests；
- strong/weak/fabricated/missing evidence fixtures。

测试也只能引用 IR 的 canonical IDs，不能手写另一套字段名称。

## 6. Link/Consistency Checker 的硬性规则

以下任一规则失败，bundle 不得进入 eval-system：

### 命名一致性

```text
所有 artifact_id 必须唯一
所有物理 path 只能由 ArtifactIR 解析
所有组件不得直接使用未注册路径
```

### Tool 一致性

```text
Runtime 暴露的 tool 必须存在于 ToolIR
ToolIR 的 input/output schema 必须和 runtime schema 一致
Task 只能引用已注册 tool
Test 只能调用已注册 tool
```

### State 一致性

```text
每个 consumed state 必须存在 producer
producer 的 output_field 必须存在
consumer 只能读取声明的 state
```

### Scorer 一致性

```text
rubric 引用的 evidence 必须存在
scorer 使用的 artifact 必须存在于 ArtifactIR
scorer 使用的 state 必须存在于 StateFieldIR
scorer 不得读取未授权路径
```

### Temporal 一致性

```text
必须在 first_business_tool_call 前产生的 artifact
不能在 runtime 结束后才生成
必须由 runtime 产生的 state
不能由 Agent 自报替代
```

### Component 一致性

```text
每个 IR component 必须有唯一 owner
同一文件不能被两个 component 写入
repair 只能修改失败 component 的文件
```

## 7. Repair 机制

旧机制：

```text
scorer 失败
→ 重写整个 EnvironmentScaffoldBundle
```

IR 机制：

```text
link/validation failure
→ 定位 IR binding
→ 定位责任 component
→ 只修复该 component
→ 重新 link
→ 重新运行受影响的 tests
```

例如：

```text
scorer 读取 synthesis，但 IR 只有 final_profile
→ binding error
→ 不是让 Scorer Agent 猜名字
→ 由 linker 报出 canonical artifact mismatch
→ Scorer Repair Agent 根据 IR 改为 final_profile
```

如果修改了 IR：

```text
IR version + 1
→ 所有受影响组件重新生成
→ 旧 bundle 标记 invalidated
```

## 8. 对当前失败案例的解释

当前实际出现过：

```python
PATHS = {
    "final_profile": "artifacts/final_profile.md",
    "resolution": "artifacts/synthesis_resolution.md",
}
```

但 scorer 后续读取：

```python
arts["synthesis"]
```

这不是单纯的 Agent “粗心”，而是系统没有提供 canonical artifact registry。

如果存在 IR，应该在 codegen 之前冻结：

```yaml
artifacts:
  - artifact_id: final_profile
    path: artifacts/final_profile.md
  - artifact_id: synthesis_resolution
    path: artifacts/synthesis_resolution.md
```

那么：

- Runtime 只能引用 `final_profile`；
- Scorer 只能引用 `final_profile`；
- Test 只能引用 `final_profile`；
- linker 可以拒绝 `synthesis` 这个未注册 ID；
- 错误在生成 bundle 之前暴露，而不是运行测试时才出现 `KeyError`。

## 9. 当前实施状态

### 已有基础

已经存在：

- `ExecutableTaskContract`；
- `EnvironmentScaffoldBundle`；
- `validate_scaffold()`；
- scorer design/review；
- candidate registry；
- component 失败后记录 validation evidence 的基础；
- RAG 知识库和 environment protocol。

### 已实现（第一阶段）

当前已经实现：

```text
EnvironmentIR models
Contract → IR lowering
IR identifier/reference validation
IR freeze/version
component output ownership checking
bundle link/consistency checking
EnvironmentCandidate staging 时持久化 IR
```

代码位置：

```text
benchmark-forge/src/benchmark_forge/environment_ir.py
benchmark-forge/src/benchmark_forge/staging.py
```

当前 IR linker 已能拒绝：

- 未 freeze 的 IR；
- 缺失 component output；
- component path collision；
- component 写入非自己拥有的路径；
- task/rubric 引用不存在的 tool/artifact/state/evidence。

### 尚未实现

还需要继续新增：

```text
LLM Contract → IR lowering agent prompt
完整 ArtifactRegistry/ToolRegistry/EvidenceRegistry schemas
IR 与生成 bundle 文件内容的深层 reference checking
component Agent 的 PydanticAI output adapters
component-level repair protocol
把 materialize_environment 改造成 IR component pipeline
```

## 10. 验收标准

IR 阶段必须至少通过：

- 所有 artifact canonical ID 唯一；
- 所有组件引用都能解析；
- 所有 tool input/output schema 一致；
- 所有 scorer evidence 引用都存在；
- 所有 rubric dimension 都绑定 evidence；
- 不允许组件自行发明 artifact 名称；
- 不允许 scorer 读取 IR 之外的路径；
- 发现 `final_profile` / `synthesis` 类命名漂移时，在 link 阶段失败；
- 单个组件失败不会导致其他组件被重写。

最终 bundle 阶段必须通过：

```text
IR validation
→ link validation
→ scaffold validation
→ import/AST smoke
→ pytest
→ scorer semantic review
→ eval-system pilot
→ agent-eval pilot
```

## 11. 一句话总结

```text
Benchmark Contract 描述“我要什么 benchmark”；
Environment IR 描述“所有组件必须如何连接”；
component agents 负责实现局部代码；
linker 负责把它们连接起来；
validation 负责证明连接没有漂移。
```

## 12. IR Compiler Agent（新增角色）

Contract 到 IR 不是纯手写 lowering。高层 Contract 中仍包含自然语言语义，尤其是：

- 工具的准确 input/output schema；
- runtime state 的 producer/consumer；
- artifact 的 canonical identity；
- evidence 的权威性；
- rubric 到 evidence/state/artifact 的绑定。

因此增加独立的：

```text
Environment IR Compiler Agent
```

它的职责不是写代码，也不是重新设计 benchmark，而是：

```text
ExecutableTaskContract
  + RAG 公开协议和实现先例
  → EnvironmentIRDraft
```

它必须补全：

- Tool Registry；
- Runtime State Registry；
- Artifact Registry；
- Evidence Registry；
- Rubric Binding；
- Task Binding；
- Component dependency graph。

Agent 输出的是未冻结的：

```text
EnvironmentIRDraft
```

Forge 随后执行：

```text
normalize_ir_draft()
→ cross-reference validation
→ freeze()
```

### 编译失败策略

IR Compiler Agent 允许 bounded rewrite：

```text
第一次生成 draft
  ↓
Forge 验证
  ↓
发现未知 artifact/tool/evidence/reference
  ↓
将确定性错误反馈给 Compiler Agent
  ↓
重新生成完整 IRDraft
```

默认最多进行有限次数 rewrite。超过次数后，返回 typed `IRCompilationError`，candidate 不能进入组件 codegen，而不是生成一个未经绑定的 bundle。

### 为什么不能只写手工 lowering

手工 lowering 只能可靠处理：

```text
已有结构化字段 → IR 字段投影
```

但不能可靠判断：

```text
“最终提交综合结果”对应哪个 canonical artifact
“正确性”由哪个 runtime state 证明
某个 rubric 是否必须依赖 tool trace
某个 tool 的返回字段和下游 state 如何绑定
```

这些是 Contract 中的自然语言语义，需要由 IR Compiler Agent 结合知识库完成；Forge 只负责机械验证和冻结。

### 当前实现

代码位置：

```text
benchmark-forge/src/benchmark_forge/ir_compiler.py
```

主要对象：

```python
EnvironmentIRCompilerAgent
IRCompilationError
```

`BenchmarkGenerationService` 已支持通过 `ir_compiler` 注入编译角色，并在 environment candidate staging 后保存冻结 IR：

```text
candidate/validation/environment-ir.json
```

当前已经实现 Compiler Agent 的调用和 bounded rewrite，但还没有把所有 component Agent 的 codegen 完全迁移到 IR pipeline。

## 13. Core IR 与可演化扩展

IR 不会试图一次性表达所有未来 benchmark。当前 `1.0` 只承诺稳定 Core：

```text
tool_registry
runtime_state
artifact_registry
evidence_authority
rubric_binding
task_binding
workspace_policy
```

`EnvironmentIR.required_features` 声明当前实例需要哪些 IR feature，`ir_version` 声明语义版本。Compiler Agent 只能在 `CORE_IR_FEATURES` 组成的语言内补全 binding。

当 Contract 中出现 Core 没有 construct 的语义时，Compiler 不会把它塞进任意 JSON，也不会通过自由文本绕过 schema，而是产生：

```python
IRExpressivenessError
```

`coordination_graph` 已作为第一个 typed extension 实现，版本为 `1.1`。它包含：

```text
IRCoordinationGraph
IRCoordinationNode
```

可以表达：

- subtask DAG；
- depends_on；
- required context；
- output contract；
- acceptance checks；
- write scope；
- parallel/assignment/attribution 要求；
- repair budget。

因此当前基本 subagent delegation Contract 可以编译到 `EnvironmentIR 1.1`。

而 `fault_injection`、`cross_session`、`resource_locking` 等仍然没有 typed construct，会显式报告：

```text
missing_features = ["fault_model"]
affected_constructs = ["constraints.fault_injection"]
```

这类错误不能进入 bounded rewrite，也不能进入 component codegen；candidate 应标记为：

```text
requires_ir_extension
```

未来增加 `CoordinationGraph`、`TemporalConstraint`、`FaultModel` 等扩展时，必须同时提供：

- Pydantic/schema 类型；
- feature/version 声明；
- 引用和 validation 规则；
- component Agent 消费方式；
- linker/validator 规则；
- 兼容旧 IR 的迁移或版本策略。

不得使用以下方式掩盖表达能力不足：

```text
extensions: dict[str, Any]
metadata: dict[str, Any]
extra fields
free-text workaround
```

## 5. 可演化 IR：Frozen 不是封闭 schema

`EnvironmentIR` 的冻结含义是“本次候选的语义绑定不可再漂移”，不是“未来语言不能扩展”。当前采用：

```text
稳定 Core IR
+ typed extension registry
+ 显式 expressiveness failure
```

Core 目前覆盖：

```text
tool_registry / runtime_state / artifact_registry
evidence_authority / rubric_binding / task_binding / workspace_policy
```

第一个真实扩展是 `coordination_graph`，版本为 `1.1`，用于表达 subagent 的 DAG、依赖、上下文、写入范围、验收检查和 repair budget。

IR 需要声明：

```yaml
ir_version: "1.1"
required_features:
  - tool_registry
  - artifact_registry
  - coordination_graph
ir_checksum: "sha256:..."
```

`EnvironmentIRCompilerAgent` 只能在已注册的 IR language 内完成 hole filling。它不能通过 `extra`、任意字典或 free-text 偷渡新语义。

当 Contract 包含当前 IR 无法表达的语义时，编译器必须返回：

```text
IRExpressivenessError
candidate.status = requires_ir_extension
```

此候选不会进入 component codegen，也不会被简化成当前 IR 能表达的低难度任务。后续应新增一个带 Pydantic 类型、引用规则和 linker 校验的 extension，再重新 compile。

## 6. Frozen IR 到组件 Agent

组件 Agent 不再分别理解完整 Contract，而是只消费同一个 Frozen IR：

```text
Manifest Component Agent → meta.yaml / README.md / tasks/
Runtime Component Agent  → core.py / mcp_server.py
Scorer Component Agent   → scorer.py / scorer_fixtures/
Test Component Agent     → tests/
```

每个 Agent 的输出协议都是严格的 `IRComponentOutput`：

```python
IRComponentOutput(
    component_id="scorer",
    files=[IRComponentFile(path="scorer.py", content="...")],
)
```

Agent 不可以返回完整 bundle，也不可以写入别的组件拥有的路径。Forge linker 负责：

1. 检查 IR 已 frozen；
2. 检查组件是否齐全且不重复；
3. 检查 path ownership 和路径碰撞；
4. 组装 `EnvironmentScaffoldBundle`；
5. 在 bundle notes 中记录 `compiled_from_ir_checksum`。

所有 codegen 必须在 `manifest → runtime → scorer → tests` 依赖顺序执行。组件级 repair 的目标也是单一组件，不能因为 scorer 失败而重写 runtime 和 manifest。

## 7. 验收与失败分类

| 失败 | 含义 | 处理 |
|---|---|---|
| Pydantic 输出格式错误 | Agent 没遵守组件协议 | 重试当前组件 |
| IR 引用不存在对象 | Draft 连接错误 | bounded rewrite Compiler Agent |
| path ownership/collision | 组件越权或重复写文件 | 当前组件重写 |
| `IRExpressivenessError` | Contract 超出 IR 语言 | 停止 codegen，升级 IR |
| bundle 静态/运行失败 | 实现与 IR 不一致 | 只 repair 失败组件 |

因此，IR 的作用不是替代 Agent，而是把 Agent 的自由度限制在一个可以验证、可以重试、可以演化的实现语言内。

## 8. 组件级 Repair

当 Verification & Control 发现 scorer 缺陷时，修复边界是：

```text
bundle
  → 提取 scorer owned paths
  → Scorer Component Agent repair
  → 用原有 manifest/runtime/tests + 新 scorer 再次 link
```

不会再让 Scorer repair Agent 重新生成完整 bundle。这样可以避免一个局部修复覆盖掉已经通过静态检查的 runtime、task 或 manifest。

当前 MVP 已将这一策略接入 scorer semantic review；后续可将同一机制扩展到 runtime、manifest 和 tests 的失败分类。

## 9. 固定 Contract 重放：不是在线重试

稳定 IR 的实验单位不是一次自然语言生成，而是一个已经冻结的 `ExecutableTaskContract`。

固定 Contract 的一次重放严格执行：

```text
已有 benchmark.json 中的 Contract
        ↓
IR Compiler Agent（单次）
        ↓
Manifest Component Agent（单次）
Runtime Component Agent（单次）
Scorer Component Agent（单次）
Test Component Agent（单次）
        ↓
linker
```

这条路径不会重新执行：

```text
Design
Grounding
Allocation
Executor Contract generation
```

也不会在运行中自动循环重试整个流程。发现 bug 后，由工程师修改 prompt、schema、normalizer、validator 或 component agent 行为，再使用**同一个 Contract**重新运行一次，用于比较：

```text
同一 Contract
→ 修复前结果
→ 修复后结果
```

入口：

```bash
PYTHONPATH=src .venv/bin/python scripts/materialize_contract_once.py \
  run/formal-component-ir-e2e-v2-20260825/benchmark.json \
  --item-index 0 \
  --output-root run/replay-fixed-contract-v1
```

输出：

```text
contract.json
environment-ir.json
bundle/
report.json
```

`report.json` 的 `failed_at` 用于定位边界：

```text
contract_loaded
ir_compiled_and_frozen
components_generated
linked
```

Agent 的内部格式重试只属于 PydanticAI 调用实现细节；固定 Contract 重放本身不属于在线自适应重试机制。

## 15. Material Registry 与 Scenario Model（IR 1.2）

2026-08-27 的空数据案例证明，仅有 Tool/Artifact/Evidence Registry 仍不足以实现数据依赖 Benchmark。IR 1.2 增加两个 typed extension：

```text
material_registry
scenario_model
```

### 15.1 IRMaterial

`IRMaterial` 固定：

```text
material_id
source_type / source_ref
target
read_only / required
visibility
schema_ref
minimum_items
collection_key
```

Manifest Component 拥有：

```text
materials/
schemas/
```

对于 `source_type=generated` 且 Agent 可见的 required material，最终 Bundle 必须实际包含对应目标文件。组件 Agent 不得只保留一个没有文件、生成器或注入 binding 的自然语言 fixture 名称。

### 15.2 IRScenario

`IRScenario` 固定：

```text
data_dependent
material_refs
runtime_generator_ref
evaluation_injection_ref
allow_empty
minimum_items
required_case_tags
case_tag_field
```

数据依赖场景至少需要一种 binding：

```text
material_refs
runtime_generator_ref
evaluation_injection_ref
```

如果 Contract 明确表达材料/场景，而 Compiler Draft 丢失对应对象，`validate_ir_contract_bindings()` 将拒绝 Draft并进入 bounded rewrite。Compiler Agent 不允许通过降低 minimum_items 或将 allow_empty 改为 true 来适配当前实现。

### 15.3 组件消费原则

```text
Manifest：实现静态 Agent-visible material/schema
Runtime：消费 material/generator/injection；缺失 required input 时 fail-closed
Scorer：缺失或不足的场景返回 invalid_environment，不产生 Agent 分数
Tests：验证 production material、最低数量、case tags 和分数区分度
```

较大的 `materials/*` 文件不会被完整复制到 Runtime/Scorer/Tests Agent prompt。依赖上下文只传递路径、字节数和 checksum 摘要；后续组件应通过 IR 和公开运行接口消费材料，避免上下文爆炸。

### 15.4 版本规则

```text
Core IR                         1.0
coordination_graph             1.1
material_registry/scenario_model 1.2
```

一个 IR 同时使用多个扩展时，`ir_version` 取所需扩展的最高版本；旧 1.0/1.1 IR 在不使用新 construct 时保持兼容。

## 16. Artifact Schema Binding（IR 1.3）

2026-08-27 的 Material/Scenario Bundle 已经拥有 6 个 production cases，但进一步审计发现：

```text
Contract：声称 final_output 是 schema-valid structured artifact
IRArtifact.schema_def：{type: object}
Manifest：只生成 fixture schema
Runtime：从 fixture 猜 output_schema；猜不到时只检查 artifact 是 dict
Scorer：猜 cases/results/responses/answers 等多个字段
Tests：使用自己构造的 cases payload
```

这不是某一个 Component Agent 的局部 bug，而是最终 Artifact 的结构没有成为共享 binding。IR 1.3 因此加入 typed feature：

```text
artifact_schema
```

每个受约束 Artifact 现在共享：

```text
IRArtifact.artifact_id
IRArtifact.path
IRArtifact.schema_path
IRArtifact.schema_def
```

职责边界：

```text
IR Compiler：把 Contract 中“structured/schema-valid”的自然语言要求补成 concrete schema hole
Manifest：在 schema_path 写出与 schema_def 完全相等的公开 JSON Schema
Task：明确告诉 Agent artifact path、schema path 和顶层输出形状
Runtime：只按 IRArtifact schema 验证，不从 fixture 任意猜字段
Scorer：按 canonical schema 读取 artifact，不接受多个猜测 alias
Tests：使用同一个 schema 构造 strong/partial/invalid payload
Link/static validator：机械检查 IR、meta、schema file、task prompt 一致
```

### 16.1 不是“只要有 JSON Schema 就算完成”

第一次真实重放中，Compiler 生成：

```json
{
  "type": "object",
  "minProperties": 1,
  "additionalProperties": true
}
```

组件完全一致，但该 schema 仍无法表达“覆盖全部 6 个 case”。因此 artifact-schema hole validator 继续要求：

```text
- object schema 必须有 named properties 和 required fields
- Contract 禁止 unrequested fields 时，每个公开 object branch 必须 additionalProperties=false
- data-dependent rubric 要求 all-case coverage 时，必须有 required per-case array
- array.minItems >= IRScenario.minimum_items
- item 至少要求 identity field + response field
```

第二次固定 Contract 重放得到：

```text
oneOf:
  - required: [cases]
    cases.minItems: 6
    cases.items.required: [case_id, response]
  - required: [clarification]
```

这使正常完成和 safe-degradation 都有明确公共协议，而不是由四个组件各自猜测。

### 16.2 版本规则更新

```text
Core IR                              1.0
coordination_graph                  1.1
material_registry / scenario_model  1.2
artifact_schema                     1.3
```

`artifact_schema` 是 typed feature，不允许通过 `metadata`、任意 extra fields 或 fixture 内隐藏自然语言代替。
