# Benchmark Forge MVP 项目目标与边界

> 状态：实现前设计草案
> 版本：MVP v0.1
> 日期：2026-08-24

## 1. 项目定位

Benchmark Forge 是一个面向自然语言评测目标的 benchmark 生成系统。它将用户的评测意图转化为一个可执行、可验证、可审计的 `Benchmark` 对象，并从已有数据集池中生成高质量 benchmark items。

MVP 的重点不是追求完全自治，而是验证以下架构假设：

1. `Benchmark` 可以作为多个角色 Agent 共享和演化的中心对象；
2. PydanticAI 可以通过结构化输出约束每个角色的决策；
3. Pydantic Graph 可以编排角色之间的有限状态流程和回路；
4. 生成结果可以通过确定性检查与语义 Verification 进行闭环验证；
5. 所有重要决策都可以通过事件和 evidence 进行回放。

## 2. MVP 目标

### 2.1 功能目标

MVP 必须能够完成以下闭环：

```text
自然语言目标
  → Benchmark 设计
  → 数据集 grounding
  → quota allocation
  → 样本生成
  → 质量检查
  → 修复或丢弃
  → verified benchmark artifact
```

### 2.2 工程目标

MVP 必须具备：

- Pydantic 类型化 domain schema；
- PydanticAI role agent；
- 有限步数和有限成本；
- 确定性的 domain validator；
- 可序列化的 Benchmark 状态；
- 结构化事件历史；
- 失败可定位，而不是静默产生错误结果；
- 可使用 mock model 运行完整测试；
- 可以和旧版 `benchagent` 进行生成质量对照。

## 3. 非目标

MVP 明确不做以下内容：

- 不实现通用 agent framework；
- 不实现新的 LLM provider SDK；
- 不实现复杂的分布式任务队列；
- 不实现 Web UI；
- 不支持任意形式的多模态生成；
- 不自动修改数据集原始内容；
- 不允许 Agent 无限制地自行创建工具；
- 不让 Agent 直接重写完整 Benchmark；
- 不把 AgentEval 改造成生成 runtime；
- 不做 benchmark rubric 自动进化；
- 不把 chain-of-thought 作为持久化产物。

## 4. MVP 范围

### 支持

- 文本数据集；
- 无现成数据时的最小程序化/合成 source provider；
- multiple choice、open ended、true/false 三种 answer type；
- 当前实现保留 Design、Grounding、Allocation、Executor、Verification & Control 五类原有角色；
- 目标架构在规划完成、materialization 前增加独立的 Preference Alignment 选择角色；
- 有限的文本转换工具；
- 本地文件数据集池；
- 空数据集池或低容量数据集下的 draft / partial / blocked 行为；
- 同步或异步单机运行；
- JSON artifact 和事件日志；
- mock LLM 测试。

### Preference Alignment 角色说明

Preference Alignment 是目标架构中的第六个辅助决策角色，但不改变原有五角色的职责：

- 原有五角色负责 benchmark 构造与验证；
- Preference Alignment 只在两个 `BenchmarkPlanCandidate` 之间预测人类偏好；
- 它不生成环境、不验证 scorer、不拥有 Preference Library；
- 当前代码尚未实现该角色，实施以 `PREFERENCE_ALIGNMENT.md` 为准。

### 暂不支持

- 真实 TTS 后端；
- 复杂图像/视频处理；
- 多机并行；
- 在线数据集检索；
- 人工实时协同界面；
- 自动化模型选择和自动成本优化。

## 5. 成功定义

MVP 不以“生成的 benchmark 全部高质量”为通过条件，而以“系统能否在不崩溃的情况下暴露真实问题”为主要目标。

MVP 通过的最低条件是：

1. 给定一个自然语言目标，即使数据集池为空或容量不足，也可以生成一个合法的 `Benchmark` draft；
2. 每个 Agent 的外部输出都有可校验的 schema，或者被记录为明确的 validation failure；
3. 结构错误不会静默污染全局状态；
4. grounding、allocation、executor、verification 的成功和失败都能进入统一事件流；
5. 单个 item 失败不会导致整个 run 丢失已有结果；
6. 最终 artifact 即使是 `partial` 或 `degraded`，也可以独立加载和检查；
7. 事件日志能够解释每个 dimension、grounding、allocation 和 item 的来源与决策；
8. 核心测试不需要真实 API key；
9. 至少一个固定 fixture 可以与旧版 baseline 做可重复对比。

以下内容属于后续质量目标，不是 MVP 硬门槛：

- 所有 dimension 都 ready；
- 所有 allocation 都满足 target size；
- 所有 item 都通过语义验证；
- 所有失败 item 都被自动修复；
- benchmark 达到生产级多样性和难度平衡。
