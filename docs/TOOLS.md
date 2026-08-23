# 工具注册与扩展

工具（tool）是转换计划的基本单元：Grounding Agent 用它构造可行的
`(dataset, transformation plan)`，Executor 在样本级编排中调用它。

工具分两类（对应论文 Sec. A.3）：

| 类别 | 特征 | 例子 |
|---|---|---|
| **LLM 工具** | 内容合成，由 LLM 驱动 | context 构建、出题、干扰项生成 |
| **纯工具（non-LLM）** | 确定性、参数化算子 | 图像缩放、噪声注入、字段修补 |

## 已注册工具（`executor/tools/registry.py`）

### LLM 工具

| 工具 | 说明 | 关键参数 |
|---|---|---|
| `context_construction` | 把原始字段组装为自包含上下文 | `input_fields` |
| `question_generation` | 由上下文生成问题 + 标准答案 | `answer_type` |
| `distractor_generation` | 生成 plausible 错误选项（MC 用） | `num_distractors` |
| `dialogue_synthesis` | 合成多说话人对话（忠实于源事实） | `num_speakers` |
| `reasoning_transform` | 把素材改写成带一个刻意错误步骤的分步推理题 | `num_steps` |

### 纯工具

| 工具 | 说明 | 关键参数 |
|---|---|---|
| `field_patch` | 结构化字段修补：增/改/删/改名 | `set` / `rename` / `delete` |
| `metadata_edit` | 元数据编辑（写入 `_meta`） | `set` |
| `content_decompose` | 字段拆分为子单元 | `source_field` / `target_field` / `delimiter` |
| `image_resize` | 图像缩放（Pillow） | `media_field` / `width` / `height` |
| `noise_injection` | 图像高斯噪声（seeded，可复现） | `media_field` / `intensity` / `seed` |
| `file_convert` | 格式转换（当前为占位） | `from` / `to` |
| `tts` | 语音合成（占位，需自行接后端） | `voice` / `style` |

## 工具接口

每个工具是一个 `Tool` dataclass：

```python
@dataclass
class Tool:
    name: str
    description: str            # 给 Grounding Agent 看的功能描述
    params_schema: dict         # 参数说明（构造计划时参考）
    is_llm: bool
    fn: ToolFn                  # (fields, params, llm, subtask) -> new_fields
```

签名约定：

```python
def my_tool(fields: dict, params: dict, llm: LLMClient, subtask: Subtask) -> dict:
    """输入当前样本字段 + 参数；返回要合并进样本状态的新字段。"""
    new = dict(fields)
    # ... 变换逻辑 ...
    return new
```

> 纯工具**不得引入新的语义信息**或改变样本的既定含义（论文定义）；
> LLM 工具通过 `llm.chat_json(system, user)` 完成内容合成。

## 新增一个工具

1. 在 `registry.py` 写变换函数
2. 在 `build_registry()` 中注册 `Tool(...)`
3. 完成——Grounding Agent 会在转换计划构造时**自动发现**它（工具描述列表是动态生成的），无需改其他代码

示例（一个把文本字段截断的纯工具）：

```python
def _pure_truncate(fields, params, llm, subtask):
    new = dict(fields)
    src = params.get("source_field")
    if src in new and isinstance(new[src], str):
        new[params.get("target_field", src)] = new[src][: params.get("max_len", 500)]
    return new

# 注册
Tool("text_truncate",
     "Truncate a text field to a maximum length (pure)",
     {"source_field": "str", "max_len": "int", "target_field": "str"},
     False, _pure_truncate),
```

## 转换计划示例

Grounding Agent 为 `(子任务, 数据集)` 生成的计划形如：

```json
{
  "steps": [
    { "tool": "context_construction", "params": { "source_fields": ["reports"] } },
    { "tool": "question_generation",  "params": { "answer_type": "multiple_choice" } },
    { "tool": "distractor_generation","params": { "num_distractors": 3 } }
  ]
}
```

Executor 的样本级编排会**按此脚手架逐样本实例化**：每步由 LLM 根据当前样本状态
决定具体参数（如从哪几个字段取上下文），但工具顺序不脱离计划，防止样本间发散。
