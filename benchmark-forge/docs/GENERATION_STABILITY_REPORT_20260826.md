# Benchmark Forge 真实生成稳定性与正确率检查

> 日期：2026-08-26  
> 范围：Benchmark 生成链路，不包含 eval-system/agent-eval 的内部调用  
> 方法：能力级真实生成 + 固定 Contract/Frozen IR 定向重放

## 1. 检查链路

```text
Design → Grounding → Allocation → Executor → Verification
→ IR Compiler → Rubric Integrity Review
→ Manifest / Runtime / Rubric / Tests
→ Linker → Static Validation → Bundle Tests
```

三个项目保持平级：Forge 只产生 bundle；本报告没有让 Forge 调用 eval-system 或 agent-eval。

## 2. 样本结果

| 能力 | 初次 Contract 生成 | 初次 IR | 修复后固定重放 | Bundle tests | Rubric Integrity |
|---|---:|---:|---:|---:|---:|
| instruction_following | 成功 | 成功 | 完整组件成功 | 暴露并修复入口/测试问题 | 尚未纳入当次旧运行 |
| robustness_fault_tolerance | 成功 | 失败 | 成功 | 8 passed | 尚未纳入当次旧运行 |
| delegation_quality | 成功 | 失败 | 成功 | 10 passed | pass, confidence 0.97 |
| context_compression_fidelity | 成功 | 成功 | Rubric + Tests 定向修复成功 | 11 passed, 2 skipped | revise → pass, confidence 0.96 |

在四个样本中：

```text
Contract 生成成功率：4/4 = 100%
首次 IR 编译成功率：2/4 = 50.0%
相同 Contract 修复/复用后 IR 可用率：4/4 = 100%
完成定向修复后的 bundle test 通过率：3/3 = 100%
Rubric Integrity 首次通过率：1/2 = 50.0%（仅统计已运行 review 的样本）
Rubric bounded revision 后通过率：2/2 = 100%
```

样本量仍小，不能当作生产统计；它用于定位系统 bug。

## 3. 阶段耗时观察

真实 telemetry 中的代表值：

| 阶段 | instruction | robustness | delegation |
|---|---:|---:|---:|
| Design | 18.749s | 26.710s | 27.748s |
| Grounding | 18.626s | 37.840s | 60.092s |
| Allocation | 5.625s | 19.827s | 13.140s |
| Executor | 36.986s | 32.542s | 46.980s |
| Verification | 28.099s | 14.495s | 21.335s |
| 首次 IR Compiler | 73.662s | 64.416s | 267.119s |

`context_compression_fidelity` 的完整角色耗时为：

```text
Design                  22.866s（另一次规划 22.364s）
Grounding               18.823s
Allocation               6.400s
Executor                 35.498s
Verification             21.057s
IR Compiler              67.444s
Rubric Integrity Review  26.937s
Manifest                 34.265s
Runtime                  65.922s
Scorer                   71.610s
初次总计约              435.76s
Tests 定向重放           61.333s
Tests bounded repair     49.137s
```

`delegation_quality` 的一次 IR 调用耗时 267.119 秒，超过配置的 90 秒。原因是 provider timeout 只限制单次请求，结构化输出修复可能产生多次请求。

已增加总 wall-clock deadline：`PydanticAIRunner` 在 Unix 主线程对整个同步 Agent 调用设置硬截止时间，避免 SDK 内部重试吞掉总预算。

## 4. 本轮发现并修复的 bug

### 4.1 旧 scorer-design 无条件运行

**现象：** Rubric 架构启用后仍执行旧复杂 scorer design，浪费约 88 秒并阻塞组件生成。  
**修复：** `enable_legacy_scorer_design=False`，旧路径仅显式兼容启用。  
**结果：** 后续完整运行 telemetry 不再出现 `role.scorer_design`。

### 4.2 新参数只修改调用点、未修改函数签名

**现象：** 真实运行出现 unexpected keyword argument。  
**修复：** 同步函数签名和两个 service 调用点。  
**结果：** 后续 instruction 运行完成到 scaffolded。

### 4.3 Flat bundle MCP 入口指向不存在的 package

**现象：** metadata/tests 使用 `foo.mcp_server`，ownership 实际只生成根目录 `mcp_server.py`。  
**修复：** normalizer 统一为 flat module；validator 拒绝缺失 package module。  
**结果：** 定向重放 bundle 可导入 MCP 模块。

### 4.4 Tests 假定 attempt artifact 已存在

**现象：** 干净 bundle 测试直接断言 `artifacts/*.json` 存在。  
**修复：** Tests prompt 要求临时 fixture；validator 检测真实 `Path("artifacts/...").is_file()` 前置依赖。  
**结果：** robustness 定向 Tests 重放后 8 passed。

### 4.5 Tests 检查实现源码字符串

**现象：** 测试要求路径字面量必须出现在 `mcp_server.py`，但合法实现将其定义在 `core.py`。  
**修复：** 禁止 source-text assertion，要求测试公开行为/常量。  
**结果：** 新 Tests Component 不再生成该脆弱检查。

### 4.6 IR 错误声明 coordination_graph

**现象：** 无 CoordinationContract 的 robustness Contract 被声明需要 coordination_graph。  
**修复：** Prompt 明确只有 typed coordination 存在时才能声明该 feature。  
**结果：** 同一 Contract 重放后 IR 和四组件成功。

### 4.7 IR state consumer 使用非法 `task_binding`

**现象：** delegation IRDraft 把 `task_binding` 放入 state consumers。  
**修复：** Prompt 明确 task 使用 state 通过 `task_binding.state_refs` 表达，consumer 只允许注册 tool/node/criterion 或 runtime/scorer/tests/agent。  
**结果：** 同一 delegation Contract 重放成功。

### 4.8 Scorer 把隐藏 IR DAG 当作唯一正确答案

**现象：** scorer 要求固定隐藏节点 ID，如 `case_facts`、`dependency_audit`；公开任务只要求 Agent 自己设计合理拆解。  
**风险：** 合理但不同的任务拆解会被错误判低分，属于 rubric/检查项偏移。  
**修复：** Scorer prompt 禁止要求未在公开 prompt 中命名的精确节点 ID、节点数或唯一 DAG；按覆盖、依赖方向、并行性、写隔离、验收和整合等性质评分。Static validator 将隐藏节点要求从 warning 升级为 error。  
**结果：** 仅重放 Scorer Component 后 static validation 无错误，bundle tests 10 passed，scorer 不再包含固定节点 ID。

### 4.9 Rubric Integrity Review 未接入主生成流程

**现象：** 已有 review 类型和 Agent 方法，但 service 未调用。  
**修复：** IR 编译后执行 review，保存 `validation/rubric-integrity-review.json`：

```text
pass   → 继续生成
revise → 记录 warning，MVP 放宽门槛后继续
reject → failed check，阻止 component codegen
```

真实 delegation rubric review：

```text
verdict = pass
target_alignment = aligned
confidence = 0.97
```

### 4.10 固定重放脚本把 invalid static validation 报成 success

**现象：** `validation.valid=false` 时 report.status 仍为 success。  
**修复：** invalid validation 现在输出 `status=failed`、`error_type=ScaffoldValidationError` 并返回非零状态。


### 4.11 Normalizer 丢失 IR 公共接口 metadata

**现象：** context compression 的初次 bundle tests 要求 `meta.yaml` 暴露 `protocol`、`task_id`、tools 和 artifacts，但旧 normalizer 只保留了部分 manifest 字段；同时它从旧 Contract scoring dimensions 恢复 rubric，会覆盖 bounded revision 后的描述。

**修复：** `normalize_octagon_scaffold(bundle, item, ir)` 接受 Frozen IR，并以 IR 生成：

```text
task_id
protocol
tools
artifacts
dimensions
```

`write_scaffold()` 和固定 Contract materialization 脚本均传入 candidate/compiled IR。

**结果：** 新 bundle 的 metadata 与 checksum `sha256:355066...e0cf` 对应的 revised IR 一致；`provenance_fidelity` 不再惩罚非最小来源集合，`state_recovery_and_completion` 明确覆盖 memory_snapshot、version、压缩预算和摘要统计。

### 4.12 Tests Agent 不知道 canonical scorer 调用接口

**现象：** 修复 source-text tests 后，新 Tests Component 用四种猜测签名调用 scorer：位置参数、`workspace=`、`artifacts_dir=`。实际公共接口是：

```python
score(*, attempt_id, task, env_db=None, trace=None, final_state=None, **kwargs)
```

Static Validation 通过，但真实 bundle tests 出现：

```text
2 failed, 9 passed, 2 skipped
TypeError: score() missing attempt_id and task
```

**修复：** Tests Component Prompt 明确 canonical keyword-only scorer interface，并禁止猜测位置参数/workspace/artifacts_dir 签名。随后仅对 Tests Component 执行 bounded repair，其他三个组件和 Frozen IR 不变。

**结果：** repaired bundle：

```text
static validation: valid
bundle tests: 11 passed, 2 skipped
core.py / mcp_server.py / scorer.py: py_compile passed
```

跳过项只表示 Runtime 没有暴露可选的惯例命名 public adapter/registry；不影响实际导入、artifact schema 和 scorer 公共接口验证。

### 4.13 受限网络环境会伪装成 Agent 超时

**现象：** 在 filesystem sandbox 内直接运行 LLM 重放，两次分别达到 180 秒和 300 秒硬截止，看起来像 Tests Agent 负担过重。

**诊断：** 最小 API 健康检查在受限环境立即返回 `Operation not permitted`；获准联网后，同一模型 4.10 秒返回，固定 Tests 重放 61.333 秒完成。

**结论：** 这两次超时不是 IR 或 Tests Agent 的生成稳定性样本。正式实验必须记录网络执行权限/连通性，并将 transport failure 与 model timeout 分开统计，避免把基础设施问题错误归因给 Agent。

## 5. 当前测试状态

```text
Benchmark Forge: 69 passed
robustness generated bundle: 8 passed
fixed delegation generated bundle: 10 passed
fixed context-compression generated bundle: 11 passed, 2 skipped
core.py / mcp_server.py / scorer.py: AST + py_compile passed
```

## 6. 当前判断

当前最明显的不稳定点已经从“Contract 无法产生”转移到：

```text
IR Compiler 的引用纪律
组件测试是否误测实现细节
组件间公共调用签名是否被 IR/角色约束明确表达
Rubric/scorer 是否把隐藏规划当唯一答案
单次 LLM 调用的总耗时边界与 transport 失败分类
```

固定 Contract/Frozen IR 的组件级重放已经能有效定位并验证修复，不需要重新运行前五个角色。

## 7. 第四个能力最终产物

```text
初次生成：
run/stability-check-context-compression-20260826/

Rubric bounded revision：
run/stability-replay-context-compression-rubric-20260826/

首次 Tests 定向重放（static valid，但 bundle tests 暴露 scorer 调用错误）：
run/stability-replay-context-compression-rubric-20260826-tests-fixed/

Tests bounded repair 最终结果：
run/stability-replay-context-compression-rubric-20260826-tests-repaired/
```

第四个样本说明当前调试闭环已经可以工作：

```text
固定 Contract / Frozen IR
→ 只重放失败组件
→ linker + deterministic normalization
→ static validation
→ 独立 bundle tests
→ 把失败反馈给同一组件 bounded repair
```

它同时说明 Static Validation 还不能替代真实 bundle tests；前者验证结构和已知反模式，后者才能发现跨组件公共调用接口是否真正一致。

## 8. 当前产量、阶段重试率与成功率

统计口径固定为本报告中的四个正式能力样本；历史 A/B fixture、旧架构原型和同一能力的废弃版本不计入当前产量。详细机器可读数据见：

```text
run/generation-statistics-20260826.json
```

### 8.1 当前产量

```text
正式 Benchmark 设计/Contract：        4
完整 bundle 目录：                    4
通过当前 static validation + bundle tests：3
已进行真实 eval-system Agent pilot：   0
promotion-ready：                      0
```

四个 Benchmark 的工程状态：

| 能力 | 当前状态 | Bundle tests |
|---|---|---:|
| instruction_following | needs_tests_repair | 7 failed, 4 passed |
| robustness_fault_tolerance | engineering_validated | 8 passed |
| delegation_quality | engineering_validated | 10 passed |
| context_compression_fidelity | engineering_validated | 11 passed, 2 skipped |

`instruction_following` 之前只记录了旧 validator 的 scaffolded 状态。本次使用当前验证器和干净目录补测，确认仍存在：

```text
缺失 package module 的 MCP import
测试要求 attempt 前 artifacts 已存在
```

因此不能把它计入可进入 pilot 的三个 bundle。

仓库 `run/` 历史数据中还有 12 个唯一 executable task ID 和 12 个非 executable fixture item，但包含旧架构原型、同一能力的不同版本和 A/B 实验样本，不能解释成 24 个当前可用 Benchmark。

### 8.2 统计定义

```text
首轮成功率 = 不做该阶段 replay/revision 就被接受的 Benchmark 数 / 进入该阶段的 Benchmark 数
重试率     = 至少需要一次可观察 replay/revision 的 Benchmark 数 / 进入该阶段的 Benchmark 数
最终成功率 = 定向修复后被接受的 Benchmark 数 / 进入该阶段的 Benchmark 数
```

同 prompt 生成两份规划用于 preference 比较属于预定采样，不算重试。Provider/PydanticAI 内部不可见的 structured-output repair 请求也没有伪装成阶段重试。

### 8.3 阶段统计

| 阶段 | 进入样本 | 首轮成功率 | 重试/修订率 | 修复后成功率 |
|---|---:|---:|---:|---:|
| Contract 五角色链路 | 4 | 4/4 = 100% | 0/4 = 0% | 4/4 = 100% |
| Environment IR compile/freeze | 4 | 2/4 = 50% | 2/4 = 50% | 4/4 = 100% |
| Rubric Integrity Review | 2 | 1/2 = 50% | 1/2 = 50% | 2/2 = 100% |
| Manifest codegen | 4 | 4/4 = 100% | 缺陷重试 0/4 | 4/4 = 100% |
| Runtime codegen | 4 | 4/4 = 100% | 0/4 = 0% | 4/4 = 100% |
| Scorer/Rubric Adapter codegen | 4 | 4/4 = 100% | 缺陷重试 1/4 = 25% | 4/4 = 100% |
| Tests + clean bundle acceptance | 4 | 1/4 = 25% | 3/4 = 75% | 3/4 = 75% |
| Linker 最终结构组装 | 4 | — | — | 4/4 = 100% |
| 当前 Static Validation | 4 | — | — | 3/4 = 75% |
| 工程可进入 Pilot | 4 | — | — | 3/4 = 75% |

补充说明：

- IR 的两个首轮失败分别是非法 `coordination_graph` feature 和非法 state consumer；固定 Contract 重放后均成功。
- Context 的 Manifest/Scorer 因上游 Rubric revision 重新生成，不算对应组件自身缺陷重试。
- Delegation 的 Scorer 因隐藏固定 DAG 偏移进行一次 scorer-only repair，因此 Scorer 缺陷重试率为 25%。
- Tests 是当前最不稳定的组件：四个 Benchmark 中只有 delegation 首轮 clean bundle acceptance 通过。
- 一次 replay 脚本遗漏 `tasks/*.json` 被 linker 拒绝，属于操作脚本错误，不计入 Agent 生成重试率。

### 8.4 尚未测量的指标

以下指标当前必须记录为 `N/A`，不能写成 0%，因为尚未进行正式运行：

```text
真实 Agent 执行成功率
真实 artifact 完成率
Rubric/Scorer 最终正确率
Benchmark 难度
模型区分度
与人类专家 Benchmark 的最终对齐度
```

当前的 75% 只表示“工程 bundle 能通过静态与自测试”，不表示 Benchmark 能正确评价 Agent，也不表示具有区分度。

## 9. 被测对象纠正：Agent，不是裸 LLM

对四个 bundle 的 tool/runtime 审计发现，之前的 Design/Executor Agent 将“开放、有工具调用”误当成了 Agent Benchmark 的充分条件，但没有先固定被测对象是完整 Agent。

新的判断是：

| 能力 | 工具构造判断 |
|---|---|
| instruction_following | 业务记录/validator 工具基本属于合法环境工具；仍有 Tests 工程问题 |
| robustness_fault_tolerance | sync/fault/state 工具属于确定性外部任务世界，构造合理 |
| delegation_quality | 无效：runtime 明确不运行 native Agent，而是模拟 subagent 状态/输出 |
| context_compression_fidelity | 对“native context compression”无效：memory_write/read 替代了 Agent 自身 context management |

因此重新统计：

```text
完整 bundle：                         4
bundle 自测试通过：                   3
被测对象 construct 合理：             2（instruction、robustness）
同时通过当前静态检查和 bundle tests：  1（robustness）
当前真正可进入目标能力 Pilot：         1
```

`delegation_quality` 可以保留为“synthetic delegation API planning”任务，但不能再标成真实 Agent 任务委派能力。`context_compression_fidelity` 可以保留为“controlled external-memory compression”任务，但不能直接代表 Agent 原生上下文压缩保真度。

根因修复已经放在 Design/Grounding/Executor/Verification prompt：明确被测对象为完整 Agent，环境只能提供任务世界和独立验证，不得重新实现目标 Agent 的原生能力。Tool ownership 是机械防线，不是对该设计原则的替代。

## 10. delegation_quality 按完整 Agent 被测对象重新生成

使用相同能力目标重新运行五角色，不复用错误旧 Contract。修正后的 Design/Grounding 明确：

```text
subagent_spawn/message/wait/trace/workspace
属于被测 Agent 原生能力

环境只提供 case materials、artifact registry
failure/conflict injector 与 acceptance verifier 属于 evaluation_system
```

首次 Executor 在 90 秒总截止时失败。只重放 Executor 后得到新 Contract；原生能力全部位于 `agent_capabilities`，没有生成 synthetic subagent runtime。随后发现 failure injector ownership 仍为 Agent 可调用环境工具，Contract Repair Agent 为修改单字段却破坏输出层级，因此最终使用可机械证明的一字段 typed patch，只修改：

```text
environment.tools[failure_conflict_injector.apply].ownership
benchmark_environment → evaluation_system
```

新 IR ownership：

```text
benchmark_environment:
  read_case
  register_artifact

evaluation_system:
  apply_injection
  verify_acceptance

agent_runtime:
  subagent_spawn
  subagent_message
  subagent_wait
  subagent_trace
  workspace_read
  workspace_write
  multi_turn_control
```

Agent public task binding 不包含 evaluation-system tools。生成 Runtime 的 MCP `tools/list` 只暴露：

```text
case_materials.read_case
artifact_registry.register
```

Runtime 没有实现 native Subagent/Workspace/Multi-turn。Rubric Integrity 首轮 `revise`，去掉超出公开目标的 dependency-inversion recovery 和 deadline 要求后第二轮 `pass`，confidence 0.98。

Tests 首次错误要求 per-tool Python function 和 Mapping scorer output；第一次 repair 又因看不到 Runtime fixture 注入接口而失败。修复组件生成协议，使 component Agent 真正接收 `depends_on` 组件输出作为接口参考；第二次 Tests-only repair 后：

```text
static validation: valid, 0 warnings
bundle tests: 4 passed
project tests: 78 passed
```

最终 corrected bundle：

```text
run/agent-subject-delegation-tests-repaired-v2-20260826/bundle/
```

它尚未完成真实 eval-system pilot，但已经不再把 fake subagent API 当成 delegation 能力。

## 自动 Materialization Workflow 更新（2026-08-26）

为减少 Codex 在生成阶段的逐案例介入，新增固定语义自动闭环：

```text
Existing Contract
→ compile/reuse Frozen IR
→ generate/reuse components
→ link
→ static validation
→ optional bundle tests
→ deterministic or Agent diagnosis
→ responsible-component-only repair
→ relink/revalidate
```

首个真实非缓存回放使用旧 `instruction_following` Contract。首次 bundle tests 为 `3 failed, 6 passed`；系统自行调用 Diagnosis Agent，将缺陷分配给 Runtime 和 Tests，各修复一次，最终 `9 passed`，`manual_interventions=0`。随后相同输入重放达到：

```text
model_calls=0
reused_ir=1
reused_components=4
bundle_tests=9 passed
```

证据：

```text
run/autonomous-materialization-instruction-20260826/first-generation-summary.json
run/autonomous-materialization-instruction-20260826/workflow-report.json
run/autonomous-materialization-instruction-20260826/aggregate.json
docs/AUTONOMOUS_MATERIALIZATION.md
```

该结果证明当前控制器可以替代一次真实的 Codex traceback 分析与定向修复过程，但样本量仍为 1；下一步应使用固定 Contract cohort 重放，而不是继续人工修单例。
