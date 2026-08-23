# 数据集接入指南

Grounding Agent 依据数据集池（`config/datasets.yaml`）为子任务寻找数据支撑。
每个数据集 = **卡片（card，元信息）** + **数据（data，加载配置）**。

## 数据集卡片字段（DatasetCard）

```yaml
card:
  dataset_id: "news_events"        # 唯一 ID
  name: "News Event Reports"       # 显示名
  modalities: ["text"]             # ["text"] | ["image","text"] | ["audio"] | ...
  io_schemas:                      # 支持的输入/输出模态组合
    - in_: ["text"]
      out: ["text"]
  size_samples: 5                  # 样本容量（配额上限的依据，务必与实际数据一致！）
  description: "一句话描述"          # 简短描述
  card_text: "多行描述"             # 给 Grounding Agent 看的详细描述（决定检索命中）
  tasks: ["event_reasoning"]       # 可支持的任务标签
  domain: "news/current events"    # 领域
```

> ⚠️ `size_samples` 必须与实际数据条数一致或更小。分配器以它为容量上限；
> 若元数据虚高，配额可能超出真实容量，导致执行阶段配额无法满足。
> 论文配置中另有 `meta` 字段可存放任意扩展信息。

## 数据加载器

`--data-root` 指定原始数据根目录，每个数据集两种加载方式：

### loader: json（推荐，结构化数据）

```yaml
data:
  loader: "json"
  path: "news_events.json"   # 相对 --data-root 解析；支持 .json 与 .jsonl
```

文件格式：顶层为数组，或 `{"samples": [...]}` / `{"data": [...]}`；
每个记录是一个样本，字段即样本字段。媒体字段（`.jpg/.png/.wav/...`）自动解析为
相对 `--data-root` 的路径。

```json
[
  { "event": "桥关闭", "reports": [ { "source": "...", "body": "..." } ] }
]
```

### loader: directory（媒体文件夹）

```yaml
data:
  loader: "directory"
  root: "my_images"          # 默认 data-root/<dataset_id>
  annotations: "ann.json"    # 可选：文件名 -> 字段
  media_field: "image_path"  # 媒体字段名，默认 media_path
```

扫描 `root` 下所有图片/音频文件，每个文件一个样本；`annotations` 可为每个文件
附加额外字段。

## 添加新数据集（三步）

1. **放数据**：原始文件放入 `--data-root` 下（或子目录）
2. **写卡片**：在 `config/datasets.yaml` 增加 `card` + `data` 条目
3. **跑一遍**：确认 grounding 能检索到它（日志中 `-> N candidate datasets`）

## 对接 Hugging Face（General-Bench 等）

当前内置 loader 为本地文件。若要直接拉取 HF 数据集（如论文使用的
General-Bench），在 `dataset_pool.py` 中注册一个 loader：

```python
def load_hf(card, cfg):
    from datasets import load_dataset
    ds = load_dataset(cfg["hf_path"], split=cfg.get("split", "train"))
    return [
        DatasetInstance(dataset_id=card.dataset_id, index=i, fields=dict(row))
        for i, row in enumerate(ds)
    ]

pool.register("hf", load_hf)   # 然后 data.loader: "hf", data.hf_path: "..."
```

安装可选依赖 `pip install "benchagent[hf]"`。

## 示例数据说明

`examples/data/` 包含两个演示数据集：

| 数据集 | 内容 | 用途 |
|---|---|---|
| `news_events.json` | 同一事件的 3 篇不同视角报道 × 5 事件 | 多视角整合/冲突消解类任务 |
| `medical_snippets.json` | 临床病例 + 诊断 + 推理链 × 6 | 领域推理/结构化问答类任务 |
