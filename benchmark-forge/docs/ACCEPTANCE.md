# Benchmark Forge MVP 验收标准与预期行为

## 1. 验收原则

MVP 使用分级门槛，避免把生产质量要求误当成开发启动条件。

### 1.1 分级门槛

### P0：必须通过

- artifact 可加载；
- Pydantic schema 和状态转换不被静默破坏；
- 不伪造 dataset/source/sample 引用；
- 每次失败都有事件和原因；
- 单个 item 失败不会丢失整个 run；
- mock model 可以完成 graph smoke test。

### P1：允许降级，但必须可观察

- grounding 不完整；
- allocation 不足 target size；
- item 语义检查失败；
- Executor 工具执行失败；
- verification 返回 warning；
- accepted item 数量少于目标。

这些情况可以产生 `partial`、`degraded`、`provisional` 或 `unverified` artifact，不要求 MVP 自动修复。

### P2：暂不作为 MVP 门槛

- 全部 dimension ready；
- 所有 item 都通过语义质量检查；
- 所有失败都自动 repair；
- target size 始终完全满足；
- 复杂难度、多样性和跨模态质量达到生产标准。


### 1.2 验收执行原则

MVP 验收以“可重复、可解释、可失败恢复”为主，不以 Agent 是否足够自由为标准。

每项验收都应尽量使用 mock model、fixture dataset 和固定 seed，避免依赖真实 API 的随机性。

## 2. 功能验收

### A. Benchmark 初始化

输入：

- 合法 user goal；
- target size；
- dataset catalog；
- run config。

预期：

- 创建唯一 benchmark_id；
- Benchmark 初始状态为 `draft`；
- user goal、运行配置和 schema version 被保留；
- 空 benchmark 可以序列化和重新加载。

### B. Design

预期：

1. Design Agent 只能返回合法的 DesignAction；
2. 新 dimension 有唯一 id；
3. 重复 dimension 被 reducer 拒绝；
4. 缺少必填字段时触发有限 retry；
5. 达到 max retries 后进入显式失败状态；
6. Design 的所有操作被写入 events；
7. Design 不会直接修改 groundings、allocations 或 items。

### C. Grounding

预期：

1. Grounding 不得伪造 dataset 或 source provider；
2. Grounding 可以选择 `existing_dataset`、`synthetic`、`hybrid`、`pending` 或 `blocked` source mode；
3. transformation / generation plan 中的工具或 provider 必须存在；
4. tool 参数不合法时不会写入 ready grounding；
5. dry-run 或 synthetic validation 失败时 grounding 进入 rejected、pending、blocked 或 provisional，而不是静默通过；
6. 某个 dimension 当前无法执行时，Benchmark 仍可保留该 dimension，并输出 draft / pending / blocked 状态；
7. 已接受或 deferred grounding 包含评分、理由和 evidence；
8. 空 dataset pool 是合法测试场景，系统不能因空池直接抛出未处理异常。

### D. Allocation

预期：

1. executable quota 总和不得超过当前可执行 capacity；
2. MVP 允许 executable/deferred quota 总和小于 target size，但必须记录 shortfall；
3. allocation 必须引用 ready、partial 或明确 deferred 的 grounding；
4. deferred quota 必须包含 source mode 和后续补足策略；
5. 非法 allocation 会被 validator 拒绝；
6. allocation 失败时可以在有限次数内重新规划；
7. 没有任何立即可执行资源时，系统应生成 pending/blocked benchmark，而不是伪造 allocation；
8. MVP 不要求 allocation 必须完全满足 target size，但必须输出缺口和原因；
9. 超过次数后生成明确的 `allocation_failed` 事件，不产生伪造 spec。

### E. Executor（Sample Realization）

预期：

1. Executor 每次只处理一个 allocation 对应的原始样本；
2. Executor 的 action 必须符合 PydanticAI 输出 schema；
3. 工具必须来自已注册的 tool registry；
4. 工具参数不合法时触发有限 retry 或标记当前 sample failed；
5. 每一步执行都产生 observation 和事件；
6. Executor 不能直接将 item 标记为 verified；
7. Executor 失败不能污染其他样本的状态。

### F. Verification & Control

预期：

1. Verification & Control 返回合法 `VerificationResult`；
2. 结构错误优先由确定性检查发现；
3. 语义问题必须包含 reasons 或 evidence；
4. 控制动作必须来自允许集合；
5. 当前样本重跑次数和 quota replenishment 次数有限；
6. 控制后可以重新进入 Executor，也可以保留 warning 后继续；
7. 最终可以进入 `verified`、`accepted_with_warnings`、`rejected`、`failed` 或 `replenishment_required` 状态；
8. 语义失败在 MVP 中不一定阻断 artifact 输出，但必须显式标记为 `unverified` 或 `degraded`。

### G. Artifact

预期：

- `benchmark.json` 可独立加载；
- `events.jsonl` 可按顺序回放；
- 每个 accepted 或 provisional item 都有 source refs；
- 每个 rejected item 都有 rejection reason；
- partial/degraded artifact 包含 shortfall 和 warning；
- manifest 包含 run id、模型配置、schema 版本、seed 和工具版本；
- artifact 重载后不会丢失状态和事件。

## 3. 非功能验收

### 3.1 可重复性

相同：

- fixture dataset；
- mock model；
- seed；
- 配置；
- graph version；

应得到等价的 Benchmark artifact。允许时间戳和 run id 不同，但 dimension、allocation、item 内容和决策事件应一致。

### 3.2 可调试性

任何失败都必须能定位到至少一个：

```text
agent
step
action
input digest
output validation error
business validation error
or tool observation
```

禁止只记录：

```text
"generation failed"
```

### 3.3 成本控制

每次运行必须记录：

- Agent 调用次数；
- tool 调用次数；
- retry 次数；
- control/retry 次数；
- warning 和 provisional item 数量；
- token usage（如果 provider 提供）；
- elapsed time。

超过预算后必须停止或进入明确的 degraded/failure 状态。

### 3.4 隔离性

- 单个 item 失败不能污染其他 item；
- 单个 dimension grounding 失败不能破坏已接受 dimension；
- 质量较差的 item 可以被标记和隔离，而不必让整个 run 失败；
- 单个 Agent 的异常不能导致无错误信息的全局崩溃；
- 并行生成时事件和 artifact 写入不能互相覆盖。

## 4. 测试矩阵

### Unit tests

- 每个 domain model 的合法/非法输入；
- 每个 action reducer；
- quota validator；
- source ref validator；
- item schema validator；
- verification result validator；
- graph router。

### Agent contract tests

每个 Agent 至少覆盖：

1. 正常结构化输出；
2. 缺字段；
3. 错类型；
4. 非法枚举；
5. 业务矛盾；
6. retry 后成功；
7. retry 耗尽后失败。

### Graph tests

MVP 必须覆盖：

1. happy path；
2. empty dataset pool → pending/blocked draft；
3. partial grounding → executable + shortfall allocation；
4. allocation shortfall → partial/degraded artifact；
5. verification warning → accepted_with_warnings；
6. executor failure → bounded replenishment；
7. checkpoint reload → continue。

以下属于后续条件回路测试，不阻断当前 MVP：

- grounding failure → design；
- allocation failure → allocation；
- verification failure → executor/control；
- insufficient accepted items → replenish；
- budget exhausted → graceful failure。

### End-to-end tests

至少准备三套 fixture：

- 简单文本 multiple choice；
- 多文档 open ended；
- 故意包含歧义或不可 grounding 的目标。

每套 fixture 都必须产生：

```text
benchmark.json
manifest.json
events.jsonl
至少一个 accepted 或明确失败原因
```

## 5. MVP 通过门槛

MVP 通过只要求 P0 条件成立：

- 核心 unit tests 通过；
- graph smoke test 通过；
- 至少一个 end-to-end happy path 通过；
- 至少三个 failure/degraded path 能按预期结束；
- artifact 可加载和回放；
- 没有未捕获且未记录的 schema validation error；
- 没有依赖真实 API 的必需测试；
- 同一 mock fixture 连续运行两次结果等价。

以下不影响 MVP 通过，但必须出现在报告中：

- generated item 少于 target size；
- provisional 或 unverified item；
- grounding pending/blocked；
- semantic verification warning；
- repair / replenishment 未完成；
- 估计成本或运行时间超出目标。

## 6. 明确的“预期行为”示例

### 示例一：正常流程

```text
用户目标合法
→ Design 产生 2 个 dimensions
→ Grounding 都找到可执行数据集
→ Allocation 满足 target size
→ Executor 生成 10 个 candidates
→ Verification 接受 8 个、标记 1 个 warning、丢弃 1 个
→ MVP 可以输出 9 个 partial/degraded items，不强制继续 replenish
```

### 示例二：数据集池为空或没有匹配数据

```text
Design 产生 multimodal_audio dimension
→ 数据集池没有音频数据
→ Grounding 检查 synthetic / procedural provider
→ 如果 provider 可用：返回 synthetic 或 hybrid grounding
→ 如果 provider 不可用：返回 blocked grounding
→ Benchmark 保留该 dimension，状态为 draft/pending/blocked
```

只有在用户要求“本次必须完整生成 target size”时，才将其升级为 run failure；否则不得因为数据集池为空而强行删除 dimension 或伪造样本。

### 示例三：题目语义不合格

```text
Executor 生成 item
→ Verification 判断答案泄漏
→ Verification & Control 返回 rewrite_question
→ Executor 按 Verification & Control 的控制建议重跑或修正当前样本
→ Verification 重新检查
```

如果仍然失败，MVP 可以二选一：

```text
item.status = discarded
replenishment_required = true
```

或在实验模式下：

```text
item.status = accepted_with_warnings
verification.status = unverified
```

### 示例四：结构化输出错误

```text
Agent 返回 quota="ten"
→ Pydantic validation failure
→ PydanticAI 触发 retry
→ 第二次返回 quota=10
→ reducer 应用
```

如果 retry 耗尽：

```text
当前 Agent 节点失败
Benchmark 保持上一个合法状态
事件记录 validation_error
Graph 按 fallback 决定重试或终止
```
