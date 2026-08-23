# benchagent

**自主评测集生成器** —— 从一句自然语言评测目标，自动产出可执行、可复现、经过验证的评测集（benchmark）。

```
用户需求 ("评估模型能否整合多方叙述并推理")
   │
   ▼
Benchmark Planner
   ├── Design Agent     需求 → 子任务集（propose → revise → discard）
   ├── Grounding Agent  数据集偏好 → 检索 → 可转换性验证（对齐/鲁棒/信号保持三维评分）
   └── Allocation Agent 配额分配（allocate → diagnose → adjust）
   │
   ▼
Benchmark Executor
   ├── Sample Planning  样本级编排（受数据集级转换计划约束）
   ├── Execution        LLM 工具 + 纯工具（图像/音频/字段操作）
   └── Verification     schema/答案类型/语义校验 + 配额补足
   │
   ▼
evaluation.json（verified benchmark）
```

本项目只负责**评测集的生成**。评测执行由下游系统消费 `evaluation.json` 自行完成。

---

## 背景

实现参照论文 **BenchmarkAgent**（*Benchmark Everything Everywhere All at Once*,
arXiv:2606.06462）。原仓库未附 LICENSE 文件、授权不明确，因此本项目是**仅依据公开论文从零实现的独立版本**，不包含原仓库代码。

## 快速开始

### 1. 安装

```bash
git clone <your-repo> && cd benchagent
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e .
```

### 2. 配置密钥

```bash
cp .env.example .env   # 填入真实值
```

```dotenv
LLM_API_KEY=sk-...
LLM_API_BASE_URL=https://api.deepseek.com/v1
```

模型调用走 **litellm**，任何其支持的提供商均可（OpenAI / DeepSeek / Anthropic /
本地 vLLM / Ollama / Azure…），提供商差异（参数支持、json mode、重试）由 litellm
统一处理。`.env` 已被 `.gitignore` 忽略，密钥永不入库。

### 3. 运行示例

```bash
benchagent \
  --query examples/user_query.json \
  --model-config config/models.yaml \
  --dataset-config config/datasets.yaml \
  --data-root examples/data \
  --cache-path cache \
  --workers 4
```

输出：`cache/multi_perspective_reasoning_demo/evaluation.json`

## CLI 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--query` | ✅ | 用户评测目标 JSON（见下方格式） |
| `--model-config` | | 模型配置，默认 `config/models.yaml` |
| `--dataset-config` | | 数据集池配置，默认 `config/datasets.yaml` |
| `--data-root` | | 原始数据根目录 |
| `--cache-path` | | 中间产物缓存目录，默认 `cache/` |
| `--workers` | | grounding 与样本执行的并行度，默认 4 |
| `--sample-cap` | | 每个数据集最多使用的原始样本数（快速冒烟用） |
| `--seed` | | 随机种子（样本打乱），默认 0 |
| `-v` | | 调试日志 |

## 配置说明

### `.env`（密钥）

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` | API 密钥（或 `OPENAI_API_KEY`） |
| `LLM_API_BASE_URL` | 端点，任意 litellm 支持的提供商 |

### `config/models.yaml`（模型选择，不含密钥）

```yaml
agents:
  default: "deepseek-v4-flash"   # 未单独指定时的兜底
  design: "deepseek-v4-flash"    # Design Agent
  grounding: "deepseek-v4-flash" # Grounding Agent
  allocation: "deepseek-v4-flash"
  executor: "deepseek-v4-flash"  # 样本编排
  verify: "deepseek-v4-flash"    # 验证层
tools:
  default: "deepseek-v4-flash"   # LLM 工具
```

每个角色可用不同模型，按成本/质量自由混搭。

### `config/datasets.yaml`（数据集池）

```yaml
datasets:
  - card:                       # 数据集元信息（DatasetCard）
      dataset_id: "news_events"
      name: "News Event Reports"
      modalities: ["text"]
      size_samples: 5           # 样本容量（配额上限依据）
      card_text: "..."          # 给 Grounding Agent 看的数据描述
      tasks: ["event_reasoning"]
      domain: "news"
    data:                       # 数据加载配置
      loader: "json"            # json | directory
      path: "news_events.json"  # 相对 --data-root 解析
```

## 产出物结构（evaluation.json）

```jsonc
{
  "query":   { "id": "...", "description": "...", "target_size": 8 },
  "spec":    {  // 可复现的 benchmark 规格
    "user_query": { ... },
    "subtasks":   [ { "id", "name", "description", "modalities",
                      "answer_type", "output_schema" } ],
    "allocations":[ { "subtask_id", "dataset_id",
                      "plan": { "steps": [ { "tool", "params" } ] }, "quota" } ]
  },
  "samples": [
    {
      "subtask_id": "corroborated_fact_identification",
      "dataset_id": "news_events",
      "sample_index": 0,
      "context": "…整合后的评测上下文（原始多篇报道）…",
      "question": "…问题（含 A/B/C/D 内嵌选项）…",
      "options": ["…", "…"],          // 与 question 字母严格对齐
      "answer": "…标准答案…",
      "answer_type": "multiple_choice",
      "media": [],                      // 多模态时存放媒体路径
      "meta": { "log": [...], "fields": { ... } }  // 生成日志 + 原始数据（审计）
    }
  ]
}
```

下游评测方只需读取 `samples`：拼 `context + question(+options)` 为 prompt → 让被测模型作答 → 与 `answer` 比对。

## 目录结构

```
benchagent/
├── benchagent/
│   ├── cli.py            # 命令行入口
│   ├── config.py         # 配置加载（.env + yaml）
│   ├── llm.py            # litellm 客户端封装
│   ├── schemas.py        # 核心数据结构（pydantic）
│   ├── prompts.py        # 所有 agent 的 prompt 模板
│   ├── dataset_pool.py   # 数据集池（卡片 + 加载器）
│   ├── pipeline.py       # 端到端编排 + 磁盘缓存
│   ├── agents/           # Planner 三 agent
│   │   ├── design.py     #   需求 → 子任务
│   │   ├── grounding.py  #   数据锚定 + 转换性验证
│   │   └── allocation.py #   配额分配
│   └── executor/
│       ├── planning.py   # 样本级编排
│       ├── verification.py  # 验证层
│       └── tools/        # 工具注册表
│           └── registry.py
├── config/               # models.yaml / datasets.yaml
├── examples/             # 示例 query + 演示数据
├── tests/smoke_test.py   # mock-LLM 端到端冒烟测试
└── docs/                 # 详细文档
    ├── API.md            # 编程接口文档
    ├── ARCHITECTURE.md   # 架构与论文映射
    ├── DATASETS.md       # 数据集接入指南
    ├── TOOLS.md          # 工具注册与扩展
    └── OUTPUT.md         # 产出物 schema 详解
```

## 可复现性

- 所有中间产物缓存于 `cache/{query_id}/`：`groundings.json`（锚定结果）、
  `spec.json`（benchmark 规格）、`evaluation.json`（最终产出）
- **断点续跑**：rerun 直接复用缓存；也可手动编辑缓存的子任务/锚定结果后继续
- grounding 结果按子任务**记忆化**：设计循环中未变的子任务不再重复验证
- grounding 与样本执行**并行**（`--workers`）

## 测试

```bash
python tests/smoke_test.py   # 无需 API key，mock-LLM 跑通全流程
```

## 扩展

- [编程接口文档（Python API）](docs/API.md)
- [接入新数据集](docs/DATASETS.md)
- [注册新工具](docs/TOOLS.md)
- [架构与论文映射](docs/ARCHITECTURE.md)
- [产出物 schema 详解](docs/OUTPUT.md)

## 已知限制

- 生成质量受数据集池覆盖度与工具集制约（论文同款限制）
- 时间线重建类题目易产生歧义，验证层可能从严拒绝（宁可少收不收歧义题）
- TTS 工具为占位实现，需自行接入后端
