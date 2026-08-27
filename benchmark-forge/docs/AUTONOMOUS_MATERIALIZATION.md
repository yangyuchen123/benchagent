# Autonomous Materialization Workflow

## 1. 目标

本机制解决的不是某一个 Benchmark 案例，而是 Benchmark Forge 自身在以下阶段需要 Codex/工程师反复读日志、定位责任组件、编写一次性修复脚本的问题：

```text
Accepted ExecutableTaskContract
→ EnvironmentIR Compiler
→ Manifest / Runtime / Scorer / Tests
→ Linker
→ Static Validation
→ optional isolated Bundle Tests
```

目标是：

```text
固定 Contract 语义
+ 固定 Frozen IR 语义
+ 自动定位组件责任
+ 只重放失败组件
+ 自动 relink / revalidate
+ 全过程可恢复、可统计
```

它**不是**在线重新设计 Benchmark 的 retry，也不会在失败后重新运行 Design、Grounding、Allocation 或 Executor Contract 设计。

## 2. 为什么之前需要 Codex 介入

之前各能力已经存在，但没有统一控制器：

- IR Compiler 能失败，却没有统一的阶段报告与恢复点；
- Component Agent 能生成四个组件，但一次失败后通常整组重跑；
- Linker 能发现路径 ownership 问题，但不会把错误路由回责任 Agent；
- `validate_scaffold()` 能发现协议问题，但工程师要手动判断是 Manifest、Runtime、Scorer 还是 Tests；
- bundle pytest 失败时，系统不能判断是测试做了错误实现假设，还是测试正确暴露了 Runtime/Scorer 缺陷；
- 成功输出没有 Contract/IR checksum 绑定的组件 checkpoint，重复运行浪费模型调用；
- 运行结果缺少统一的“零人工介入率”和分阶段失败统计。

因此实际流程是：

```text
系统生成
→ Codex 查看 report / pytest
→ Codex 判断责任组件
→ Codex 写一次性 replay 脚本
→ Agent 重写组件
→ Codex 再次 link / test
```

新的控制器把中间人工编排固化进系统。

## 3. 稳定边界

### 3.1 不允许自动变化

```text
Design
Grounding
Allocation
Accepted ExecutableTaskContract
Frozen EnvironmentIR semantic checksum
```

组件修复 Agent 只能返回：

```text
IRComponentOutput(component_id=manifest|runtime|scorer|tests)
```

如果问题不能在现有 Frozen IR 下修复，流程停止；不能通过简化任务、改 rubric 目标、改 artifact 名称或发明 IR 字段来“修好”。

### 3.2 允许自动变化

```text
Manifest-owned files
Runtime-owned files
Scorer-owned files
Tests-owned files
```

每次只修改 diagnosis 指定的组件，并重新经过 linker 和 static validation。

## 4. 自动诊断

诊断分两层。

### 4.1 机械诊断优先

无需 LLM 的明确映射包括：

| Failure evidence | 责任组件 |
|---|---|
| linker 报告 `component tests does not own path` | Tests |
| `tests/...` 要求预存 attempt artifact | Tests |
| `scorer.py` syntax/signature/hidden criterion | Scorer |
| `meta.yaml`、task JSON、entrypoint 引用错误 | Manifest |
| `core.py`、`mcp_server.py`、实现了 `agent_runtime` 工具 | Runtime |

机械诊断减少一次额外模型调用，并避免 Diagnosis Agent 随意扩大写入范围。

### 4.2 歧义诊断 Agent

例如 pytest 失败：

```text
tests/test_runtime.py failed
```

不能机械地认定 Tests 错了，因为测试可能正确暴露 Runtime 缺陷。此时调用 `Materialization Diagnosis Agent`，其结构化输出只能是：

```text
repair_components
retry_generation
stop
```

并只能选择四个已有 component ID。它无权修改 Contract 或 IR。

## 5. Checkpoint 与恢复

产物：

```text
contract.json
environment-ir.json
environment-ir-checkpoint.json
components/manifest.json
components/runtime.json
components/scorer.json
components/tests.json
bundle/
workflow-events.jsonl
workflow-report.json
```

IR checkpoint 同时绑定：

```text
contract_checksum
ir_checksum
```

Component checkpoint 同时绑定：

```text
contract_checksum
ir_checksum
component_id
```

只有两个 checksum 都相同才允许重用。因此：

- 暂时网络失败后重新运行，不重复生成已成功组件；
- Tests-only repair 不重复生成 Manifest/Runtime/Scorer；
- Contract 或 IR 变化后，旧组件自动失效；
- 不会把相同 `environment_id` 误当作相同语义。

## 6. 与三个平级项目的边界

```text
benchmark-forge
  产生 Contract / IR / bundle / workflow report

 eval-system
  消费 bundle，在隔离环境运行 Agent 和生成代码
  产出 attempt / trace / artifact / test result

agent-eval
  消费 attempt evidence 与 rubric
  产出 benchmark quality / score report
```

Benchmark Forge 不调用另外两个项目的内部代码。`BundleTestBackend` 是接口：生产环境应由隔离执行方实现，并把版本化结果作为产物交回；`LocalPytestBackend` 只用于显式启用的开发自测。

## 7. 使用

固定已有 Contract 回放：

```bash
.venv/bin/python scripts/replay_contract_autonomously.py \
  path/to/benchmark.json \
  --item-index 0 \
  --output-root run/my-fixed-replay
```

开发环境显式运行生成 bundle 的 pytest：

```bash
.venv/bin/python scripts/replay_contract_autonomously.py \
  path/to/benchmark.json \
  --output-root run/my-fixed-replay \
  --run-local-tests
```

重复执行同一命令会重用 checksum 匹配的 IR 和成功组件。

汇总多次流程：

```bash
.venv/bin/python scripts/summarize_materialization_runs.py run/ \
  --output run/materialization-summary.json
```

## 8. 流程验收指标

`workflow-report.json` 记录：

```text
status
manual_intervention_required
model_calls
ir_compile_attempts
reused_ir
generated_components
reused_components
repaired_components
linker_attempts
static_validation_attempts
bundle_test_attempts
automatic_diagnoses
agent_diagnoses
manual_interventions
```

聚合时重点看：

```text
ready_rate
zero_human_intervention_rate
manual_intervention_rate
stage_failure_rate
每个 ready bundle 的 model call 数
component checkpoint reuse rate
```

MVP 建议验收门槛：

```text
固定 Contract replay 最终 ready rate                 ≥ 80%
零人工介入率                                           ≥ 70%
明确静态 ownership 错误自动路由正确率                  = 100%
修复时 Contract checksum 与 Frozen IR checksum 不变化 = 100%
重复运行成功样本的 IR/组件重用率                       = 100%
IRExpressivenessError 后继续 component codegen 的比例  = 0%
```

## 9. 仍然需要人的地方

自动化不会假装解决 Benchmark validity：

- Contract 是否测对了能力；
- 新语义是否应升级 IR；
- 正式 Agent pilot 后是否有难度与区分度；
- rubric 是否与人类专家标准对齐；
- 达到修复预算后仍然跨组件歧义的失败。

这些是治理或实验结论，不应被 component repair Agent 偷偷决定。新的目标不是完全取消人，而是把人从逐文件调试移到真正需要判断的语义边界。
