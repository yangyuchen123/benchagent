# 编程接口文档（Python API）

benchagent 既提供 CLI（`benchagent` 命令），也暴露完整的 Python API，可嵌入
自定义流程。本文档覆盖所有公共接口：签名、参数、返回值与用法示例。

```
快速导航
├── 端到端入口    run_pipeline() / Pipeline
├── 数据模型      schemas.py（pydantic）
├── LLM 客户端    LLMClient
├── 配置          load_model_config()
├── 数据集池      DatasetPool
├── Planner Agent DesignAgent / GroundingAgent / AllocationAgent
├── Executor      SamplePlanner / Verifier / state_to_sample
└── 工具注册      Tool / build_registry()
```

---

## 1. 端到端入口

### `run_pipeline()` — 一键生成（推荐）

```python
from benchagent.pipeline import run_pipeline

samples = run_pipeline(
    query_path="examples/user_query.json",   # 用户目标 JSON
    model_config_path="config/models.yaml",
    dataset_config_path="config/datasets.yaml",
    data_root="examples/data",               # 原始数据根目录
    cache_path="cache",                      # 缓存目录
    seed=0,                                  # 随机种子
    sample_cap=None,                         # 每数据集样本上限（测试用）
    workers=4,                               # 并行度
)
# -> Optional[list[BenchmarkSample]]，None 表示流程失败
print(len(samples or []))
```

### `Pipeline` — 分阶段控制

```python
from benchagent.config import load_model_config
from benchagent.dataset_pool import DatasetPool
from benchagent.pipeline import Pipeline
from benchagent.schemas import UserQuery

model_config = load_model_config("config/models.yaml")
pool = DatasetPool.from_config("config/datasets.yaml", data_root="examples/data")

pipe = Pipeline(
    model_config=model_config,   # load_model_config() 的返回值
    dataset_pool=pool,           # DatasetPool 实例
    cache_path="cache",
    llm=None,                    # 可注入自定义 LLMClient（测试用 mock）
    seed=0,
    sample_cap=None,
    workers=4,
)
query = UserQuery(id="demo", description="...", target_size=8)

spec    = pipe.plan(query)               # -> Optional[BenchmarkSpec]  仅规划
samples = pipe.execute(spec)             # -> list[BenchmarkSample]    仅执行
# 或一步到位：
samples = pipe.run(query)                # -> Optional[list[BenchmarkSample]]
```

`Pipeline.run()` 内部会写 `evaluation.json` 到 `cache/{query_id}/`；返回的
`BenchmarkSample` 列表与文件内容一致。

---

## 2. 数据模型（`benchagent.schemas`）

全部为 pydantic v2 模型，支持 `.model_dump()` / `.model_validate()`。

| 模型 | 关键字段 | 说明 |
|---|---|---|
| `UserQuery` | `id: str`, `description: str`, `target_size: int` | 用户评测目标 |
| `DatasetCard` | `dataset_id`, `name`, `modalities: list[str]`, `io_schemas`, `size_samples: int`, `description`, `card_text`, `tasks: list[str]`, `domain`, `meta: dict` | 数据集元信息；`modality_label` 属性返回 `"text"` 等拼接 |
| `DatasetInstance` | `dataset_id: str`, `index: int`, `fields: dict` | 一条原始样本 |
| `Subtask` | `id`, `name`, `description`, `modalities: list[str]`, `answer_type`（`multiple_choice`/`open_ended`/`true_false`）, `output_schema: dict`, `status`（`proposed`/`grounded`/`rejected`） | 一个评测子任务 |
| `TransformStep` | `tool: str`, `params: dict` | 转换计划的一步 |
| `TransformPlan` | `steps: list[TransformStep]`, `rationale: str` | 数据集级转换计划 |
| `Grounding` | `subtask_id`, `dataset_id`, `plan: TransformPlan`, `scores: dict` | 论文符号 `(s, d, t)` |
| `AllocationItem` | `subtask_id`, `dataset_id`, `plan`, `quota: int` | `(s, d, t, q)` |
| `BenchmarkSpec` | `user_query: UserQuery`, `subtasks: list[Subtask]`, `allocations: list[AllocationItem]` | 论文的 `B = {(sᵢ, Gᵢ)}` |
| `SampleState` | `index`, `subtask_id`, `dataset_id`, `fields: dict`, `status`（`pending`/`done`/`failed`/`verified`）, `log: list[str]` | 样本在执行中的可变状态 |
| `BenchmarkSample` | `subtask_id`, `dataset_id`, `sample_index`, `question`, `context?`, `media: list`, `options?`, `answer`, `answer_type`, `meta: dict` | 最终评测样本（即 evaluation.json 的 samples 元素） |

示例：

```python
from benchagent.schemas import UserQuery, Subtask

q = UserQuery(id="demo", description="评测多视角推理", target_size=100)
s = Subtask(
    id="fact_integration", name="Fact Integration",
    description="整合多方叙述中的共同事实",
    modalities=["text"], answer_type="multiple_choice",
    output_schema={"question": "str", "options": "list", "answer": "str"},
)
```

---

## 3. LLM 客户端（`benchagent.llm.LLMClient`）

litellm 封装，三个方法覆盖全部调用模式：

```python
from benchagent.config import load_model_config
from benchagent.llm import LLMClient

client = LLMClient(load_model_config("config/models.yaml"))
# 若需指定模型/端点：LLMClient({"api_key": "...", "base_url": "...", "default_model": "..."})

text = client.chat(system, user, model=None, temperature=0.2, max_tokens=None)  # -> str
obj  = client.chat_json(system, user, model=None, temperature=0.2, max_tokens=None)  # -> Any(JSON)
choice = client.chat_enum(system, user, ["A", "B", "C"], model=None, temperature=0.0)  # -> str
```

| 方法 | 返回 | 说明 |
|---|---|---|
| `chat()` | `str` | 普通对话补全 |
| `chat_json()` | 解析后的 JSON 对象 | 强制 JSON 输出；自动兜底"prompt 需含 json 字样"、markdown 围栏/裸对象提取 |
| `chat_enum()` | `choices` 中之一 | 限定选项输出，温度 0 |

- 异常：`LLMError`（解析失败）；网络/限流错误自动重试（tenacity，指数退避，最多 5 次）
- 空响应防御：若带 `max_tokens` 返回空，自动去掉该参数重试一次
- 换提供商只需改 `.env` 的 `LLM_API_BASE_URL` / `LLM_API_KEY`，代码不变

---

## 4. 配置（`benchagent.config`）

```python
from benchagent.config import load_model_config, load_yaml

cfg = load_model_config("config/models.yaml")
# cfg 键：api_key, base_url, default_model, agents{dict}, tools{dict}
#   - api_key / base_url 来自 .env（相对 config 路径解析），yaml 不含密钥
#   - agents.design / grounding / allocation / executor / verify 为各角色模型名
#   - default_model 为兜底模型

raw = load_yaml("config/datasets.yaml")   # 通用 yaml 读取
```

---

## 5. 数据集池（`benchagent.dataset_pool.DatasetPool`）

```python
from benchagent.dataset_pool import DatasetPool
from benchagent.schemas import DatasetCard

pool = DatasetPool.from_config("config/datasets.yaml", data_root="examples/data")

pool.cards                       # dict[dataset_id, DatasetCard]
pool.add_card(DatasetCard(...))  # 编程式注册卡片
pool.register("json", my_loader) # 注册自定义加载器
instances = pool.load("news_events", {"loader": "json", "path": "news_events.json"})
# -> list[DatasetInstance]
```

加载器签名：`loader(card: DatasetCard, cfg: dict) -> list[DatasetInstance]`。
内置：`json`（JSON/JSONL 文件）、`directory`（媒体文件夹）。

---

## 6. Planner Agent

### DesignAgent（`benchagent.agents.design`）

```python
from benchagent.agents.design import DesignAgent
from benchagent.schemas import UserQuery, Subtask

agent = DesignAgent(llm, model="gpt-4o")

subtasks = agent.propose(query, dataset_summary)      # -> list[Subtask]
subtasks = agent.revise(query, subtasks, feedback)    # -> list[Subtask]
subtasks, feedback = agent.run(query, dataset_summary, grounding_validate)
# grounding_validate: Callable[[list[Subtask]], tuple[bool, str]]
#   run() 内部最多 4 轮：propose → (grounding 反馈 → revise) 循环
```

### GroundingAgent（`benchagent.agents.grounding`）

```python
from benchagent.agents.grounding import GroundingAgent

agent = GroundingAgent(llm, pool, model=None, registry=None, workers=4)
# registry 默认 build_registry()

gs = agent.ground_subtask(subtask)     # -> list[Grounding]（可能为空 = 无法锚定）
ok, feedback, groundings = agent.validate(subtasks, memo=None)
# groundings: dict[subtask_id, list[Grounding]]
# memo: dict[(subtask_id, description), list[Grounding]]，跨轮次缓存
```

### AllocationAgent（`benchagent.agents.allocation`）

```python
from benchagent.agents.allocation import AllocationAgent

agent = AllocationAgent(llm, pool, model=None)
allocations = agent.run(query, subtasks, groundings)   # -> list[AllocationItem] | None
spec = agent.build_spec(query, subtasks, groundings)   # -> BenchmarkSpec | None
# 可行性约束：总量=target；单对配额 ≤ 数据集容量；每子任务占比 ≥ 15%；最多 3 轮
```

---

## 7. Executor

### SamplePlanner（`benchagent.executor.planning`）

```python
from benchagent.executor.planning import SamplePlanner, state_to_sample
from benchagent.executor.tools.registry import build_registry

planner = SamplePlanner(llm, subtask, transform_plan, build_registry(), model=None)
state = planner.run(raw_instance, index)   # -> SampleState
#   state.fields 初始为 raw 字段 + answer_type；执行计划各步后合并新字段
#   state.status: done / failed

sample = state_to_sample(state)            # -> BenchmarkSample | None
# 关键约定：MC 选项顺序以 question 内嵌字母为准（_embedded_options），
# 保证 options 数组与 question 字母严格对齐；open_ended 时 options=[]
```

### Verifier（`benchagent.executor.verification`）

```python
from benchagent.executor.verification import Verifier

verifier = Verifier(llm, model=None)
ok, msg = verifier.verify(subtask, sample)   # -> (bool, str)
# 1) 确定性结构检查：必填字段、MC 选项≥2 且无重复、答案在选项中、
#    内嵌选项与 options 一致、open_ended 不带选项
# 2) LLM 语义检查：可作答、忠实于上下文、答案不被问题泄露
```

### 工具注册（`benchagent.executor.tools.registry`）

```python
from benchagent.executor.tools.registry import Tool, build_registry, tool_descriptions

registry = build_registry()          # -> dict[str, Tool]
registry["context_construction"]     # 按名取工具
text = tool_descriptions(registry)   # 给 Grounding Agent 看的描述文本

# Tool dataclass:
#   name: str, description: str, params_schema: dict,
#   is_llm: bool, fn: Callable[[dict, dict, LLMClient, Subtask], dict]
# fn(fields, params, llm, subtask) -> 要合并进样本状态的新字段
```

新增工具 = 写一个 `fn` + 在 `build_registry()` 注册一行（详见 docs/TOOLS.md）。

---

## 8. 完整编程式示例（端到端）

```python
"""把 benchagent 嵌入你的流程：生成 → 读取样本 → 交付下游评测。"""
from benchagent.config import load_model_config
from benchagent.dataset_pool import DatasetPool
from benchagent.pipeline import Pipeline
from benchagent.schemas import UserQuery

# 1. 构建
model_config = load_model_config("config/models.yaml")
pool = DatasetPool.from_config("config/datasets.yaml", data_root="examples/data")
pipe = Pipeline(model_config=model_config, dataset_pool=pool, cache_path="cache")

query = UserQuery(id="my_bench", description="评估模型跨语言多跳推理", target_size=50)
samples = pipe.run(query)
if samples is None:
    raise SystemExit("生成失败，请查看日志")

# 2. 消费（samples 即 evaluation.json["samples"]）
from collections import Counter
per_subtask = Counter(s.sample_index for s in samples)  # 占位示意
for s in samples:
    print(s.subtask_id, "|", s.question[:60], "|", s.answer[:30])

# 3. 序列化交付
import json
payload = {"query": query.model_dump(),
           "samples": [s.model_dump() for s in samples]}
json.dump(payload, open("deliverable.json", "w"), ensure_ascii=False, indent=2)
```

---

## 依赖注入与测试

`Pipeline(..., llm=...)` 接受任意实现了 `chat / chat_json / chat_enum` 三方法的
对象，便于用 mock 做离线测试（参见 `tests/smoke_test.py`）：

```python
class FakeLLM:
    def chat(self, system, user, **kw): return "reply"
    def chat_json(self, system, user, **kw): return {"subtasks": []}
    def chat_enum(self, system, user, choices, **kw): return choices[0]

pipe = Pipeline(model_config=cfg, dataset_pool=pool, llm=FakeLLM())
```
