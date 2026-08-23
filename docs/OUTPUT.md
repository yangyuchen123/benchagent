# 产出物 schema 详解（evaluation.json）

`cache/{query_id}/evaluation.json` 是最终交付物，三层结构：

```
evaluation.json
├── query    # 用户目标（原样回显，便于溯源）
├── spec     # benchmark 规格（可复现生成的完整信息）
└── samples  # 最终评测样本（核心交付）
```

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | object | 用户查询 |
| `spec` | object | BenchmarkSpec：子任务 + 分配 |
| `samples` | array | 通过验证的评测样本 |

## query

```jsonc
{
  "id": "multi_perspective_reasoning_demo",  // 唯一标识（也是缓存目录名）
  "description": "Build a benchmark that evaluates whether a model can integrate ...",
  "target_size": 8                            // 目标样本总数
}
```

## spec.subtasks[]

```jsonc
{
  "id": "corroborated_fact_identification",   // 子任务 ID
  "name": "Corroborated fact identification",
  "description": "识别被 ≥2 个独立报道佐证的事实 ...",  // 评测意图
  "modalities": ["text"],
  "answer_type": "multiple_choice",            // multiple_choice | open_ended | true_false
  "output_schema": {                           // 预期输出字段
    "question": "...",
    "options": ["..."],
    "answer": "..."
  }
}
```

## spec.allocations[]

```jsonc
{
  "subtask_id": "corroborated_fact_identification",
  "dataset_id": "news_events",
  "plan": {                                    // 数据集级转换计划（脚手架）
    "steps": [
      { "tool": "context_construction", "params": { "source_fields": ["reports"] } },
      { "tool": "question_generation",  "params": { "answer_type": "multiple_choice" } },
      { "tool": "distractor_generation","params": { "num_distractors": 3 } }
    ],
    "rationale": "..."
  },
  "quota": 3                                   // 该 (子任务, 数据集) 配额
}
```

## samples[]

每个样本的字段与下游使用方式：

```jsonc
{
  "subtask_id": "corroborated_fact_identification",  // 归入哪个子任务（可按此分组统计）
  "dataset_id": "news_events",                        // 数据溯源
  "sample_index": 0,                                  // 原始数据下标
  "context": "Event: City bridge closure\n\nReport 1 ...",  // 评测上下文（必给）
  "question": "Based on the provided news reports, which of the following facts is ...\n\nA. ...\nB. ...",
  "options": ["The bridge will be closed ...", "..."],  // 与 question 内嵌字母严格对齐
  "answer": "The bridge will be closed to all traffic starting Monday.",
  "answer_type": "multiple_choice",
  "media": [],                                    // 多模态时为 [{type, path}, ...]
  "meta": {                                       // 审计信息
    "log": ["step0: context_construction -> ['context']", "..."],  // 生成步骤日志
    "fields": { "event": "...", "reports": [...] }                 // 原始数据
  }
}
```

### 字段约定（下游务必遵守）

1. **`options` 与 `question` 的字母顺序一致**：若 question 内嵌 `A./B./C./D.`，
   则 `options[0]` 对应 A、`options[1]` 对应 B，依此类推。验证层已保证此约束。
2. **`answer_type = open_ended` 时 `options` 为空数组 `[]`**，`answer` 为参考解答
   （表述可能不唯一，下游可选用 LLM-as-judge 判定）。
3. **`answer` 是选项原文**（非字母序号），直接比对或按字母映射皆可。
4. **`context` 自包含**：题目在无外部知识时亦可作答；`media` 非空时需连同媒体输入。

### 典型评测流程（下游）

```
for sample in evaluation.json["samples"]:
    prompt = sample["context"] + "\n\n" + sample["question"]
    if sample["options"] and question 未内嵌选项:
        prompt += 格式化后的 "A. opt1\nB. opt2 ..."
    output = target_model(prompt)
    predicted = 解析 output（选项文本优先，字母兜底）
    correct = (predicted == sample["answer"])
按 subtask_id 分组统计准确率
```

## 缓存目录其他文件（非交付物，供调试/续跑）

```
cache/{query_id}/
├── groundings.json     # 每子任务的数据集锚定结果（含评分）
├── spec.json           # benchmark 规格（= evaluation.json 的 spec）
└── evaluation.json     # 最终交付物
```
