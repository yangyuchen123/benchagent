# Capacity Batch 真实实验发现（2026-08-26）

> 状态：实验记录，不是最终质量结论。
>
> 本文记录使用 Agent Capacity Library 抽取多个能力后，真实运行 Benchmark Forge 的发现。由于样本量很小，所有比例都只能作为工程诊断指标，不能作为能力质量的统计结论。

## 1. 实验目的

单个 benchmark 不能暴露跨 Contract 的稳定性问题。本轮从 capacity library 中抽取四个能力，尝试运行：

```text
capacity definition
  → natural-language goal
  → Design
  → Grounding
  → Allocation
  → Executor Contract
  → Verification
  → IR Compiler
  → Manifest / Runtime / Scorer / Tests
  → linker
```

实验关注：

- 各角色和组件的耗时；
- Contract 是否保持开放可执行，而不是选择题；
- IR Compiler 是否能把不同 Contract 降低为合法 Frozen IR；
- 组件是否能在同一 IR 下组装；
- Scorer 是否能通过语义审核；
- 失败是否停在正确边界，而不是绕过 IR 进入旧流程。

## 2. 实验输入与数据位置

本轮计划能力：

```text
instruction_following
robustness_fault_tolerance
delegation_quality
context_compression_fidelity
```

批处理脚本：

```text
scripts/run_capacity_batch.py
```

实验目录：

```text
run/capacity-batch-20260826/
```

每个能力目录中：

```text
benchmark.json       # Contract 生成阶段的完整 benchmark artifact
checkpoint.json      # 阶段 checkpoint
 events.jsonl        # 角色和编排事件
telemetry.jsonl      # 每次模型调用耗时和状态
batch-record.json    # 本能力批次结果
 environment-candidates/ # 候选和检查状态
```

## 3. 当前实际完成情况

### 3.1 instruction_following

已完成到：

```text
Contract       ✅
Verification   ✅
IR             ✅（该批次后来暴露了流程缓存问题）
Components     ✅
Scorer review  ✅（verdict 需要 repair）
```

这个样本最初的 IR 失败原因是：

```text
state inspected_record_ids references unknown consumers: coverage_check
```

之后通过固定 Contract replay，已定位并修复相关引用约束。该批次的旧 `batch-record.json` 保留了修复前结果，不应被当作修复后的最终成功率。

### 3.2 robustness_fault_tolerance

完整通过了：

```text
Contract
→ Verification
→ Frozen IR
→ Manifest
→ Runtime
→ Scorer
→ Tests
→ linker
→ Scorer review
```

当前状态：

```text
needs_repair
```

原因不是 bundle 无法组装，而是 `scorer_semantic_review` 未通过。说明当前主要瓶颈已经从“组件互相无法连接”转移到了“评分器是否真正测量目标能力”。

### 3.3 delegation_quality

已完成：

```text
Design
Grounding
Allocation
Executor Contract
Verification
```

IR Compiler 首次返回：

```text
UnexpectedModelBehavior: Exceeded maximum output retries (0)
```

因此没有得到有效 Frozen IR。此样本证明 delegation Contract 本身可以生成，但 IR Compiler 对复杂 coordination Contract 的输出稳定性仍不足。

### 3.4 context_compression_fidelity

本轮尚未开始有效运行。批处理在第三个能力的旧流程污染问题暴露后停止，以避免继续消耗模型调用并混淆统计。

## 4. 真实阶段耗时

### instruction_following

```text
Design              15.2s / 19.8s
Grounding           19.7s
Allocation           5.3s
Executor            29.2s
Verification        19.3s
IR Compiler         58.7s
Scorer Design       69.1s
Manifest            42.7s
Runtime             73.5s
Scorer             177.2s
Tests               42.3s
Scorer Review       46.2s
```

### robustness_fault_tolerance

```text
Design              18.1s
Grounding           23.9s
Allocation           7.0s
Executor            65.3s
Verification        14.9s
IR Compiler         67.0s
Scorer Design       77.1s
Manifest            40.8s
Runtime             58.9s
Scorer              77.4s
Tests               53.6s
Scorer Review       45.0s
```

### delegation_quality（部分）

```text
Design              31.3s
Grounding           53.6s
Allocation          16.4s
Executor            49.1s
Verification        17.6s
IR Compiler         18.9s，输出协议失败
```

## 5. 主要发现

### 5.1 Contract 生成比 IR 生成稳定

三个已启动能力都能生成开放式 executable Contract，并通过 Verification 返回有效或带 warning 的结果。当前自然语言到 Contract 不是主要失败点。

但这不等于 Contract 已经可以直接执行。Verification 仍可能指出：

```text
generated_contract
implementation 缺失
source 可审计性不足
```

所以 Contract 只能作为高层规格，不能代替 IR 和 bundle。

### 5.2 IR Compiler 是跨能力稳定性的关键边界

不同能力的 Contract 会暴露不同类型的 IR draft 错误：

```text
tool.read_material       # 非法 canonical ID
自由语义 state consumer   # 未注册对象
错误的 component ownership
未知 required_features    # Agent 把字段误报成 feature
```

当前处理原则：

```text
Contract 语义超出 IR
→ IRExpressivenessError
→ requires_ir_extension
→ 停止 codegen

Agent draft 不符合现有 IR
→ validator error
→ bounded rewrite 或本次失败
```

未知 feature 与 Contract 表达能力缺口已经区分，避免把 Agent 自己发明的 feature 当成真实 IR 演化需求。

### 5.3 Component ownership 必须由 Forge 固定

Agent 曾经把以下语义对象写到 `owned_paths`：

```text
runtime.case_pack_loaded
rubric.evidence_synthesis_coordination
artifacts/report.json
```

这些不是组件文件 ownership。现在固定为：

```text
manifest: meta.yaml, README.md, tasks/
runtime:  core.py, mcp_server.py
scorer:  scorer.py, scorer_fixtures/
tests:   tests/
```

结论：

> Agent 可以填充 IR 语义 binding，但不能重新定义组件物理边界。

### 5.4 Linker 能有效暴露组合错误

固定 Contract replay 已成功完成：

```text
IR Compiler
→ 四个 Component Agent
→ linker
→ Octagon 静态 validation
```

这证明引入 IR 和 linker 后，组件之间的命名漂移不再只能在 eval-system 运行时发现。

### 5.5 Scorer 是目前最明显的质量瓶颈

两个完整组件样本都能生成 scorer，但 semantic review 均需要 repair。Scorer 还出现最高单次耗时：

```text
177.2s
```

后续应进一步把 scorer 约束为：

```text
evidence reader
criterion evaluator
fallback policy
result formatter
```

不应让 Scorer Agent 再次自由理解完整 benchmark。

### 5.6 失败后 fallback 会污染实验统计

发现旧流程存在对象缓存问题：

```text
IR compile failed
→ registry 已清除 environment_ir
→ 内存中的 candidate 仍保留旧 IR
→ materialization 错误地继续执行
```

结果是 IR 失败样本仍出现：

```text
scaffold_integrity passed
scorer_semantic_review failed
```

这会把“绕过 IR 的旧 bundle”错误计算为组件生成结果。

已经修复：materialization 前重新从 registry reload candidate。现在启用 IR Compiler 时：

```text
IR 不存在
→ 不调用 scorer design
→ 不调用 legacy whole-bundle materializer
→ 本候选停止
```

## 6. 当前工程指标（非统计结论）

按目前已启动的三个能力粗略观察：

| 阶段 | 观察 |
|---|---|
| Contract 生成 | 3/3 返回 executable Contract |
| Verification | 3/3 返回有效结果 |
| IR Compiler | 2 次成功、1 次输出协议失败；样本仍太少 |
| Manifest Component | 已完成样本均返回 |
| Runtime Component | 已完成样本均返回 |
| Scorer Component | 已完成样本均返回，但语义审核存在问题 |
| Test Component | 两个完整组件样本均返回 |
| Scorer semantic review | 返回稳定，但尚未观察到 pass |
| Promotion-ready | 0 |

这些比例只能表示当前工程调试状态，不能表示模型能力或 benchmark 质量的最终统计结果。

## 7. 下一轮实验协议

后续实验必须遵守：

```text
每个能力独立目录
每个 Contract 独立 artifact_root
IR 失败立即停止该候选
不调用 scorer design
不调用 legacy materializer
不把失败候选计入 component 成功率
继续下一个能力
```

对于已经生成的 Contract，可以单独执行：

```text
已有 Contract
→ IR Compiler（单次）
→ Manifest / Runtime / Scorer / Tests（各单次）
→ linker
→ static validation
```

修复 prompt、schema、normalizer、validator 或 linker 后，必须使用相同 Contract 和新的输出目录重跑，才能比较修复前后效果。

## 8. 当前结论

当前系统已经从：

```text
一个 Agent 生成完整环境
```

推进到：

```text
多个角色生成 Contract
→ IR Compiler 建立共享协议
→ 多个组件 Agent 局部生成
→ linker 机械组装
→ 静态校验
```

真实多能力实验显示：

```text
Contract 生成基本可用
IR 需要继续稳固
组件组装已经可用
Scorer 语义质量仍需提升
失败隔离必须严格执行
```

下一步目标不是继续扩大系统复杂度，而是用更多固定 Contract 验证：

```text
同一 IR 规则是否跨能力稳定
不同能力是否会触发不同的 IR 缺口
Scorer 是否能在不重写整个 bundle 的情况下修复
组件失败是否总能停在正确边界
```

## 9. 第二批运行与流程修复

在第一批发现 IR 失败后仍可能调用旧 materializer 的缓存问题后，增加了严格隔离：

```text
IR 编译失败
→ 清除 candidate.environment_ir
→ 重新加载 candidate
→ 不调用 scorer design
→ 不调用 legacy whole-bundle materializer
→ 该能力立即结束
```

批处理脚本现在支持选择能力：

```bash
PYTHONPATH=src .venv/bin/python scripts/run_capacity_batch.py \
  --capabilities delegation_quality context_compression_fidelity \
  --output-root run/capacity-batch-20260826-v2
```

第二批已启动 `delegation_quality`，确认新的流程已经进入 Design/Grounding 阶段；因为单个真实模型调用耗时较长，本轮在完成完整结果前停止，未将其计入成功率。后续可从独立输出目录继续运行，避免污染第一批结果。

## 10. 当前数据解释规则

第一批 `batch-record.json` 中的旧结果包含修复前的流程污染，只能用于发现 bug，不能用于最终阶段正确率。修复后的统计必须满足：

```text
candidate 有 Frozen IR
→ 才计入 component generation denominator
IR 失败
→ 只计入 IR failure，不计入 component failure
Scorer review failed
→ 计入 bundle 组装成功，但 scorer semantic failure
```

在样本量增加前，不报告最终 benchmark 成功率；只报告每个边界的原始计数、失败类型和耗时。

## 11. 第三个完整能力：context_compression_fidelity

第三个完整样本位于：

```text
run/capacity-batch-20260826-v3/context_compression_fidelity/
```

结果：

```text
Contract       ✅
Verification   ✅
Frozen IR      ✅
Manifest       ✅
Runtime        ✅
Scorer         ✅
Tests          ✅
Scorer review  ✅（verdict 需要 repair）
```

批次状态：

```text
benchmark status = degraded
candidate status = needs_repair
```

这里的 `degraded` 不是 Contract、IR 或 linker 失败，而是当前生成服务在 scorer semantic review 未通过后，按候选治理规则保留为待修复状态。这个样本进一步确认：

```text
跨不同能力，IR → 组件组装已经连续成功；
当前主要质量瓶颈是 Scorer 的语义正确性，而不是文件组装。
```

阶段耗时：

```text
Design              28.4s
Grounding           18.8s
Allocation          11.7s
Executor            39.2s
Verification        24.4s
IR Compiler         86.5s
Scorer Design       173.6s
Manifest            35.1s
Runtime             70.1s
Scorer              87.5s
Tests               29.7s
Scorer Review       51.8s
```

这个样本中 `Scorer Design` 成为最长阶段之一，说明在进入 Scorer Component Agent 之前，Verification & Control 的评分方案设计也需要进一步限制输出范围。

## 12. 更新后的原始计数

目前已经完整跑过 materialization/review 的能力：

```text
instruction_following
robustness_fault_tolerance
context_compression_fidelity
```

`delegation_quality` 已生成 Contract 并完成部分阶段，但尚未得到干净的 IR/组件完成结果，仍不计入组件成功率分母。

在三个完整样本中观察到：

```text
Contract 生成：3/3
Verification 返回：3/3
Frozen IR：2/3（instruction 的旧记录受修复前流程影响；robustness/context 为当前干净成功）
Manifest：2/2 干净样本
Runtime：2/2 干净样本
Scorer：2/2 干净样本
Tests：2/2 干净样本
Scorer review 返回：3/3，但当前记录均需要进一步 repair
```

这些数字仍然只用于工程调试，不能作为最终统计结论。

## 13. 评分架构调整

多能力实验进一步验证：要求 Scorer Component Agent 为每个 benchmark 生成复杂的确定性验收程序，已经成为不合理的工程负担。后续主要评分方式改为：

```text
Runtime/eval-system 收集 evidence
→ deterministic normalizer
→ frozen rubric + Evidence Package
→ LLM Rubric Evaluator
```

Scorer Component 降级为 evidence adapter，不再负责自由设计复杂状态机。详细边界见：

```text
docs/SCORING_ARCHITECTURE.md
```

这不是让 LLM 根据常识随意打分。Rubric 仍由 Contract → IR 冻结，LLM 只能引用公开 Evidence Package；证据不足必须输出 `insufficient_evidence`。
