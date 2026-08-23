# 架构与论文映射

本文档说明 benchagent 的架构设计，以及各组件与论文
*Benchmark Everything Everywhere All at Once*（arXiv:2606.06462）Sec. 3 的对应关系。

## 总体数据流

```
用户需求 R (自然语言)
   │
   ▼  ┌──────────────── Benchmark Planner ────────────────┐
   │  Design Agent                                       │
   │   propose → revise → discard  (子任务集 S={s_i})     │
   │        │ 失败（存在无法锚定的子任务）                    │
   │        ▼ 反馈                                        │
   │  Grounding Agent                                    │
   │   i) 偏好 → 检索 → 过滤  (候选数据集 D_i)             │
   │   ii) 转换计划构造 → 评分筛选 (grounding (s,d,t))     │
   │   iii) ∀s_i ∃可行 grounding？ ──否──▶ 返回 Design     │
   │        │ 是                                          │
   │        ▼                                            │
   │  Allocation Agent                                   │
   │   allocate → diagnose → adjust (配额 q_ij)          │
   │   B = {(s_i, G_i)}，G_i = {(d_ij, t_ij, q_ij)}      │
   └───────────────┬──────────────────────────────────────┘
                   ▼  ┌──────────────── Benchmark Executor ────────────────┐
                     Sample Realization                                  │
                     i) 编排：LLM 按数据集级计划 t_ij 决定样本下一步动作      │
                     ii) 执行：LLM 工具 / 纯工具，中间输出回馈编排           │
                     ▼                                                  │
                     Verification & Control                              │
                     - schema / 答案类型 / 语义对齐                       │
                     - 配额不足 → 补充轮（重新选择原始样本重跑）             │
                     ▼                                                  │
                   evaluation.json（verified benchmark）                │
                     └────────────────────────────────────────────────────┘
```

## 组件与论文对应

| 论文组件 | 本项目实现 | 关键行为 |
|---|---|---|
| Design Agent（Propose/Revise/Discard） | `agents/design.py` | 需求拆解为 1-4 个原子子任务；收到 grounding 反馈后修订，**允许减少子任务数量**并保留已验证成功的子任务；最多 4 轮 |
| Grounding Agent（Preference/Searching/Transformability/Score-and-Filter） | `agents/grounding.py` | 每子任务：偏好描述 → 数据集卡片评分检索（≥3.0 入选，取前 5）→ 生成转换计划 → 按 **alignment≥4 / robustness≥3 / signal_preservation≥3** 阈值筛选；全子任务有 grounding 才接受 |
| Allocation Agent（Allocate/Diagnose/Adjust） | `agents/allocation.py` | 配额分配；可行性检查：总量=target、单对不超容量、每子任务占比≥15%；不满足则诊断后重分配，最多 3 轮 |
| Sample Planning（编排） | `executor/planning.py` | LLM 依据数据集级计划 + 当前样本状态决定下一步（工具+参数）；计划是**脚手架**，防止样本间发散 |
| Execution | `executor/tools/registry.py` | LLM 工具直接调用；纯工具按已实例化参数确定性执行；中间输出回馈编排 |
| Verification | `executor/verification.py` | 结构检查（确定性、零成本）+ 语义检查（LLM）；配额不足触发补足轮 |

## 关键设计决策

### 1. 多阶段 LLM 输出的数据一致性
每个 agent 的输出都是 pydantic 校验的 schema；阶段间传递的字段有**单一事实来源**。
典型例子：multiple-choice 的选项顺序只以 `question` 内嵌的 A/B/C/D 为准
（`executor/planning.py::_embedded_options`），验证层拒绝内嵌选项与 options 数组
不一致的样本——避免"同一道题两种选项顺序"的错位问题。

### 2. 优雅降级（Discard 的工程化）
设计循环重试耗尽后，**丢弃无法锚定的子任务**，保留已成功锚定的子集继续执行
（`pipeline.py` 中的 salvage 逻辑），而不是整体失败。

### 3. 成本控制
- grounding 按 `(subtask_id, description)` 记忆化：设计循环中未变的子任务不重复验证
- grounding 与样本执行并行（`--workers`，ThreadPoolExecutor）
- 结构校验为确定性代码，不烧 LLM 调用；LLM 只用于语义校验

### 4. 可复现
全流程磁盘缓存（`cache/{query_id}/`），可断点续跑、可手动编辑中间产物。

## 目录职责

```
benchagent/
├── cli.py            参数解析、日志、调用 pipeline
├── config.py         .env 密钥 + yaml 模型配置加载
├── llm.py            litellm 封装：chat / chat_json / chat_enum，重试与兜底
├── schemas.py        UserQuery / DatasetCard / Subtask / Grounding / TransformPlan /
                      AllocationItem / BenchmarkSpec / SampleState / BenchmarkSample
├── prompts.py        全部 system prompt 与 user 消息构造器
├── dataset_pool.py   DatasetPool：卡片注册 + json/directory 加载器
├── pipeline.py       编排 Design→Grounding→Allocation→Execute；缓存；salvage
├── agents/           Planner 三 agent（见上表）
└── executor/         样本编排、验证、工具注册表
```

## 关于评测执行

本项目**只生成评测集**。`evaluation.json` 是自包含交付物：下游评测系统读取
`samples` 后自行构造 prompt、调用被测模型、比对答案。项目内不含评测执行代码，
以保证生成器与评测方解耦。
