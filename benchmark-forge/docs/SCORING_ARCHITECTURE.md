# Benchmark 评分架构调整：从生成 Scorer 程序到 LLM Rubric Evaluator

> 日期：2026-08-26
> 状态：架构调整提案，优先用于当前 MVP

## 1. 问题判断

当前流程要求 Agent 生成：

```text
复杂 benchmark
+ 多种 evidence
+ 多个 rubric criterion
→ scorer.py
→ 可执行验收程序
```

这把三个不同难度的问题压缩到了一个 Scorer Component Agent：

```text
证据如何产生
证据如何读取
复杂行为如何判断
```

真实实验已经显示：

- Scorer Component Agent 的耗时明显高于其他组件；
- Scorer semantic review 在多个能力样本中都需要 repair；
- benchmark 的 Contract、IR、Manifest、Runtime 和 linker 已经可以成功组装，但 scorer 仍是主要质量瓶颈。

因此当前主要问题不是 benchmark 不够复杂，而是要求模型把开放行为完整编译成复杂的确定性验收程序，负担过重。

## 2. 新的主要评分路径

后续主路径改为：

```text
Agent 执行 benchmark
    ↓
Runtime / eval-system 收集原始 evidence
    ↓
Deterministic Evidence Normalizer
    ↓
Frozen Rubric + Evidence Package
    ↓
LLM Rubric Evaluator
    ↓
结构化维度评分
```

其中：

```text
LLM 不负责设计 benchmark
LLM 不负责发明 rubric
LLM 不负责猜测缺失证据
LLM 只根据冻结 rubric 评价已经收集的公开 evidence
```

## 3. Scorer Component 的新职责

Scorer Component 不再负责实现复杂的行为验收逻辑，只负责：

```text
1. 声明需要哪些 evidence
2. 将 eval-system 的 trace/state/artifact 映射为标准 Evidence Package
3. 检查必需 evidence 是否存在
4. 计算简单、机械、无争议的字段
5. 调用或暴露统一的 Rubric Evaluator 输入
```

它不应该再生成：

```text
复杂任务状态机
隐含答案推断器
多分支行为判断程序
基于字符串的脆弱验收逻辑
```

## 4. 两层评分

### 4.1 Deterministic Evidence Layer

这一层只处理机器可以明确判断的事实：

```text
文件是否存在
JSON 是否可解析
artifact schema 是否符合
工具是否被调用
调用顺序
调用次数
并行时间窗口
状态字段是否出现
attempt 是否结束
资源用量
```

输出统一的 Evidence Package：

```json
{
  "schema_version": "benchmark-forge.evidence-package.v1",
  "attempt_id": "...",
  "artifacts": [...],
  "tool_events": [...],
  "state_transitions": [...],
  "agent_events": [...],
  "resource_usage": {...},
  "deterministic_checks": [...]
}
```

### 4.2 LLM Rubric Layer

这一层处理难以用固定程序表达、但可以由专家 rubric 评价的内容：

```text
计划是否完整
任务拆解是否合理
委派合同是否清晰
证据是否支持结论
不确定性是否诚实
修复是否针对失败原因
压缩后是否保留关键关系
```

LLM 只能从 Evidence Package 中选择证据，并输出：

```json
{
  "criterion_id": "decomposition_completeness",
  "score": 82,
  "confidence": 0.84,
  "evidence_refs": ["artifact:plan", "agent_event:assignment_3"],
  "reason": "...",
  "uncertainties": ["..."],
  "verdict": "pass"
}
```

## 5. Rubric 必须在评分前冻结

Rubric 来源只能是：

```text
Benchmark Contract
→ Environment IR
→ frozen rubric
```

运行中的 LLM Evaluator 不得：

```text
新增 criterion
修改权重
降低 pass threshold
把缺失 evidence 当作通过
用自己的常识替代 rubric
```

如果 evidence 不足，必须输出：

```text
insufficient_evidence
```

而不是猜测。

## 6. Scorer 与 LLM Evaluator 的边界

```text
Scorer Adapter:
  事实提取、格式规范化、证据打包、机械检查

LLM Rubric Evaluator:
  根据冻结 rubric 对公开 evidence 做结构化判断

Verification & Control:
  审核 rubric 是否可公开满足、evidence 是否足够、LLM evaluator 是否越权

agent-eval:
  执行 evaluator，保存原始 evidence、prompt 版本、rubric 版本和 evaluator 输出
```

注意：LLM Evaluator 不能主动请求人类介入，也不能主动提出 preference。人类对齐仍然是离线流程。

## 7. 对当前组件流程的影响

新的组件流程：

```text
Manifest Agent
  → task prompt + rubric metadata

Runtime Agent
  → tools + observable state + event protocol

Evidence Adapter Agent / Component
  → evidence package adapter

Test Agent
  → evidence schema / adapter / evaluator contract tests

LLM Rubric Evaluator
  → 在 eval-system / agent-eval 中运行
```

当前的 `scorer.py` 不立即删除，而是降级为兼容入口：

```text
scorer.py
  → 生成或读取 Evidence Package
  → 调用统一 evaluator adapter
```

它不能再由 Agent 自由编写复杂评分状态机。

## 8. 评分结果的治理

必须同时保存：

```text
raw evidence
normalized evidence package
frozen IR checksum
rubric checksum/version
evaluator model/version
prompt checksum
evaluator structured output
```

这样才能区分：

```text
执行失败
证据收集失败
rubric 无法判断
LLM evaluator 判断失败
```

## 9. 迁移顺序

```text
Phase 1:
  固定 EvidencePackage schema
  固定 RubricEvaluation schema
  暂不改变 eval-system

Phase 2:
  Scorer Agent 只生成 evidence adapter
  禁止生成复杂 acceptance state machine

Phase 3:
  agent-eval 接入 LLM Rubric Evaluator
  保留 deterministic checks 作为硬约束

Phase 4:
  对同一批 attempts 比较：
  旧 scorer.py
  vs LLM rubric evaluator

Phase 5:
  人工离线抽样评价 evaluator 与专家 rubric 的一致性
```

## 10. 当前结论

当前 benchmark 生成系统不应该继续要求 Agent 为每个 benchmark 发明复杂 scorer 程序。

更合理的抽象是：

```text
Benchmark Agent 负责设计任务
IR Agent 负责冻结连接协议
Runtime 负责产生可观察证据
Deterministic layer 负责整理事实
LLM Evaluator 负责根据冻结 rubric 做高层判断
```

这会降低生成负担，同时保留开放 benchmark 所需的复杂行为评价能力。

## 10. 本轮实现状态（2026-08-26）

本轮已将上述边界落到代码，而不只是设计：

- `IRRubricCriterion` 现在保留 `weight`、`minimum_score`、`critical_gate`，避免 Contract 降低到 IR 时丢失评分语义。
- `EvidencePackage.evidence_bindings` 明确记录：IR 的逻辑 evidence ID 如何映射到本次运行产生的具体 evidence ID。例如 `evidence_artifact → artifact:final_report`。
- `normalize_evidence()` 对缺失 artifact、缺失 canonical tool trace 生成确定性诊断；它不会读取主机目录，也不会从任意嵌套字典猜事实。
- 统一 EvidencePackage 只定义跨模块可交换的证据协议；实际的 `eval-system EvalSample` 适配器不放在 Benchmark Forge 内，而由消费方（通常是 agent-eval 的离线 adapter）实现。
- 新增 `validate_rubric_evaluation()`。它机械校验 IR/rubric/checksum/attempt 身份、criterion 集合和重复项、evidence binding、权重聚合、minimum score、critical gate、threshold 与 overall verdict。
- `LLMRubricEvaluator` 在接受模型输出前必须经过该校验，因此 LLM 无法通过改变总分、标准或引用不存在的 evidence 来绕过冻结 rubric。

当前验证结果：

```text
62 passed
```

仍未完成的真实闭环是：在实际 `eval-system` 运行产生的 `EvalSample` 上，使用每个已组装环境的 IR 加载并评价，再把结果写回 `agent-eval` 的统一报告。下一步应优先做该批量适配，而不是继续增加 scorer agent 的自由度。

## 11. 平级模块边界（修订）

`benchmark-forge` 不导入或调用 `eval-system`，也不负责把一次运行转换成
`EvalSample`。三者通过文件和版本化数据产物协作：

```text
benchmark-forge → Benchmark Bundle + frozen rubric + evidence requirements
                    ↓
eval-system      → attempt artifacts + runtrace + verifier result
                    ↓
agent-eval       → EvidencePackage + RubricEvaluation + report
```

`EvidencePackage` 在 Forge 中只作为中立交换协议定义；面向具体
`eval-system` 的 adapter 必须放在消费运行产物的一侧。

Rubric 在 Forge 中的生成时检查由 `RubricIntegrityReview` 完成。它只检查：

```text
目标是否对齐
检查范围是否过大或过小
评价方向是否反转
证据要求是否原则上可观察
```

它不是 scorer generator，也不读取 attempt，不请求人类介入。通过后才进入
materialization；`revise/reject` 只表示 rubric 需要修正或不能表达目标。
