# BenchmarkBundle 协议设计（v2 提案）

> 状态：设计提案，尚未替换现有 v1 产物。
>
> 目标：让 `benchagent/benchmark-forge`、`eval-system`、`agent-eval` 和
> `agent-octagon` 通过一个可寻址、可校验、可分层挂载的发布产物协作，且不让
> 任一运行后端的私有格式成为跨项目协议。

## 1. 结论先行

现有 `benchmark.json`、`EnvironmentCandidate/candidate.json`、生成的环境目录和
`evaluation.json` 不能继续共同充当 BenchmarkBundle：它们分别是生成状态、候选治理
状态、Octagon 运行目录和旧静态样例格式，语义层级不同，且没有一个不可变的发布边界。

建议新增唯一的发布协议：

```text
BenchmarkBundle v2
├── bundle.json                 # 唯一入口、索引、版本、兼容性、完整性
├── lock.json                   # 解析后的依赖/运行时/引用锁定
├── specs/
│   ├── benchmark.json          # 发布后的公开 benchmark 摘要
│   ├── ir.json                 # 冻结 Environment IR，跨组件的语义连接源
│   └── tasks/<item_id>.json    # TaskSpec 投影；一项一个稳定任务
├── public/                     # 可挂载给被测 Agent 的任务材料
├── runtime/                    # 环境实现/adapter；由 eval-system 调用
├── evaluator/                  # agent-eval 读取的 rubric/evidence contract
│   ├── rubric.json
│   └── evidence.json
├── private/                    # 仅 evaluator 可读；不得进入 Agent workspace
├── tests/                      # bundle 自检，不是 attempt 产物前置条件
└── checksums.json              # 文件级 hash + bundle digest
```

`benchmark.json`（Forge 内部状态）和 `candidate.json`（候选治理记录）仍可保留，
但只能作为生成/审批证据，不能被 `eval-system` 或 `agent-eval` 当作执行输入。

## 2. 四个项目的责任边界

| 项目 | 读取 | 产出 | 不应负责 |
|---|---|---|---|
| benchagent / Forge | goal、source、环境目录/RAG | `BenchmarkBundle v2`、生成 provenance、validation | 调 runtime、读取 attempt、执行评分 |
| eval-system | bundle manifest、TaskSpec、runtime entrypoint | `TrialResult v1`、attempt artifacts、runtrace、verifier facts | 理解 benchmark 评分语义、修改 task/rubric |
| agent-octagon | runtime projection 或 bundle runtime | 执行 Agent、提供环境状态/轨迹/原始 verifier 数据 | 定义 bundle 语义、决定最终分数 |
| agent-eval | `TrialResult` + evaluator contract + private refs | `EvidencePackage`、`RubricEvaluation`、report | 依赖 Octagon 私有 DB/目录结构 |

兼容已有 Octagon 的方式是提供一个**投影器**：

```text
BenchmarkBundle v2 -> agent-octagon env directory (legacy projection)
BenchmarkBundle v2 -> eval-system TaskSpec/EnvironmentSpec
TrialResult       -> agent-eval EvidencePackage
```

投影器带自己的 `projection.v1` 版本；投影结果不是新的事实源。

## 3. 设计不变量

1. **单一语义源**：任务、工具、状态、artifact、evidence、rubric 的 ID 和 schema
   只在冻结 `specs/ir.json` 中定义；`meta.yaml`、task 文件和代码不得重新发明它们。
2. **发布不可变**：`bundle_id + version + bundle_digest` 唯一标识一个发布；修改任何
   公开任务、runtime、rubric 或 private fixture 都必须产生新版本和新 digest。
3. **逻辑 ID 优先**：下游引用 `artifact_id/evidence_id/criterion_id`，物理路径只是
   manifest 的实现映射；禁止 scorer 通过猜路径取证。
4. **显式可见性**：每个文件声明 `public`、`runtime`、`evaluator` 或 `private`；
   Agent workspace 只能获得 public 材料和 runtime 的必要接口，不能获得 evaluator/private。
5. **声明式挂载**：任务材料、workspace、网络、工具和超时都由 bundle 声明；运行后端
   不能因为自身默认值静默改变任务语义。
6. **事实与判断分离**：eval-system 只产出运行事实；最终分数和 verdict 由 agent-eval
   依据冻结 rubric 重算。
7. **未知版本拒绝或显式降级**：未知 `schema_version`、缺引用、hash 不匹配不能静默猜测。
8. **attempt 前置状态与 attempt 产物分离**：`tests/` 可以用 fixture 自检；任务要求的
   artifact 必须由 Agent attempt 生成，不能在 bundle 初始化阶段伪造为已存在。

## 4. `bundle.json` 最小结构

以下是协议形状，不要求字段顺序；JSON hash 使用 canonical JSON（UTF-8、sorted keys、
无空白分隔符）：

```json
{
  "schema_version": "benchmarkbundle.v2",
  "bundle_id": "bb_context_compression_fidelity",
  "version": "1.0.0",
  "benchmark_id": "context-compression-fidelity",
  "title": "Context compression fidelity",
  "status": "released",
  "producer": {
    "name": "benchmark-forge",
    "version": "0.2.0",
    "source_revision": "git:...",
    "generated_at": "2026-08-26T00:00:00Z"
  },
  "compatibility": {
    "task_spec": ["taskspec.v1"],
    "environment_spec": ["envspec.v1"],
    "trial_result": ["trialresult.v1"],
    "required_projection": ["octagon-projection.v1"]
  },
  "entrypoints": {
    "ir": "specs/ir.json",
    "tasks": "specs/tasks/index.json",
    "runtime": "runtime/entrypoint.json",
    "evaluator": "evaluator/index.json",
    "lock": "lock.json",
    "checksums": "checksums.json"
  },
  "items": [
    {
      "item_id": "context_compression_fidelity_001",
      "task_id": "bench/context-compression-fidelity/001@1.0.0",
      "task_ref": "specs/tasks/context_compression_fidelity_001.json",
      "runtime_ref": "runtime/entrypoint.json",
      "evaluator_ref": "evaluator/index.json",
      "public_material_refs": ["public/items/context_compression_fidelity_001/"],
      "private_material_refs": ["private/items/context_compression_fidelity_001/"]
    }
  ],
  "integrity": {
    "algorithm": "sha256",
    "manifest_digest": "sha256:<digest-of-canonical-manifest-without-digest>",
    "checksums_ref": "checksums.json"
  }
}
```

`bundle.json` 只做索引和发布元数据，不嵌入大量 prompt、代码或答案。这样可以避免
同一内容在 `BenchmarkItem`、`meta.yaml`、`tasks/*.json` 三处复制后发生漂移。

## 5. TaskSpec 与 IR 的关系

### 5.1 TaskSpec 是 eval-system 的输入投影

`specs/tasks/<item_id>.json` 应直接满足或可无损投影为 `taskspec.v1`，至少包含：

```json
{
  "schema_version": "taskspec.v1",
  "task_id": "bench/context-compression-fidelity/001@1.0.0",
  "name": "context-compression-fidelity-001",
  "version": "1.0.0",
  "instruction": "...",
  "instruction_checksum": "sha256:...",
  "source": "benchmark-forge:...",
  "content_ref": {"type": "path", "path": "public/items/context_compression_fidelity_001"},
  "metadata": {
    "item_id": "context_compression_fidelity_001",
    "bundle_id": "bb_context_compression_fidelity",
    "ir_checksum": "sha256:..."
  },
  "output_schema": {
    "artifacts": ["final_state", "decision"]
  },
  "task_checksum": "sha256:..."
}
```

不要再由 `eval-system` 根据 `query.id/subtask/sample_index` 临时生成 task ID；ID 应由
Forge 在发布时冻结。`task_checksum` 覆盖 instruction、公开输入、输出契约、运行约束和
IR 引用，而不覆盖 private answer 内容；private 内容在 `checksums.json` 单独校验。

### 5.2 IR 是跨组件连接协议，不是运行后端协议

`specs/ir.json` 采用现有 `EnvironmentIR`，至少包括：

```text
EnvironmentIR
├── tool registry (tool_id, input/output schema, ownership, trace event)
├── runtime state (state_id, path, producer, consumers, authority)
├── artifact registry (artifact_id, path, media type, schema, producer, lifecycle)
├── evidence registry (evidence_id, source type, authority, read interface)
├── rubric binding (criterion -> evidence/artifact/state refs)
├── task binding (task -> tool/artifact/state/evidence refs)
├── workspace/security policy
└── component graph and provenance
```

IR 的 `ir_checksum` 是**冻结语义内容** hash。任何 `meta.yaml` 或代码生成后改动 IR
语义都必须失败，而不是在 normalize 阶段覆盖回去。

## 6. Runtime 与 evaluator 的接口

### 6.1 runtime/entrypoint.json

```json
{
  "schema_version": "runtime-entrypoint.v1",
  "kind": "octagon_env | python_adapter | container | external_registry",
  "root": "runtime/octagon-env",
  "command": ["python", "-m", "mcp_server"],
  "healthcheck": {"command": ["python", "-m", "pytest", "tests/test_protocol.py"]},
  "requirements": {
    "python": ">=3.11,<3.15",
    "network": "forbidden",
    "timeout_seconds": 600
  },
  "owned_components": {
    "manifest": ["meta.yaml", "tasks/"],
    "runtime": ["core.py", "mcp_server.py"],
    "tests": ["tests/"]
  }
}
```

命令必须引用 bundle 内真实存在的文件/模块；发布校验器需要检查 command、import、task
路径和 ownership，禁止把 `python -m foo.mcp_server` 写入一个只有根目录
`mcp_server.py` 的 flat bundle。

### 6.2 evaluator/index.json

```json
{
  "schema_version": "evaluator-contract.v1",
  "rubric_ref": "evaluator/rubric.json",
  "evidence_ref": "evaluator/evidence.json",
  "scoring_owner": "agent-eval",
  "deterministic": true,
  "private_mount": "private/",
  "legacy_scorer": {
    "ref": "runtime/octagon-env/scorer.py",
    "role": "compatibility_verifier_only",
    "authoritative": false
  }
}
```

`rubric.json` 保存冻结的 criterion、weight、minimum score、critical gate、threshold；
`evidence.json` 保存每个 criterion 允许读取的 evidence binding。agent-eval 必须按
这些 ID 取证并重算总分，不能接受 runtime scorer 或模型直接提交的总分。

如果暂时必须让 agent-octagon 执行旧 `scorer.py`，它只能产出 raw verifier facts；
这些 facts 经过 TrialResult 交给 agent-eval，不能把 Octagon 的 scorer 输出当作最终分数。

## 7. 文件可见性、挂载和私有数据

推荐三种视图：

```text
agent-view      = public/ + runtime/公开接口（不含 evaluator/、private/）
execution-view  = agent-view + eval-system 必需的环境实现
scoring-view    = bundle 全部 + private/ + TrialResult
```

`private/` 可以放 expected answer、隐藏 fixture、评分器私有配置；但必须：

- 不被 `content_ref` 指向 Agent workspace；
- 不被 runtime 的公开工具直接列目录或读取；
- 在 `private-manifest.json` 中声明用途、hash 和 owner；
- 由 eval-system/Octagon 的 adapter 通过显式 mount/ref 接收，而非依赖当前工作目录；
- 若后端无法保证隔离，bundle validator 必须将状态标记为 `degraded`，禁止发布为 `released`。

这修复了旧 `materialize_benchmark_tasks()` 把 `tests/expected.json` 与 Agent 可见任务目录
放在同一棵树的问题。

## 8. 完整性与可复现性

`checksums.json` 示例：

```json
{
  "schema_version": "bundle-checksums.v1",
  "algorithm": "sha256",
  "files": {
    "specs/ir.json": "sha256:...",
    "specs/tasks/context_compression_fidelity_001.json": "sha256:...",
    "runtime/core.py": "sha256:...",
    "evaluator/rubric.json": "sha256:...",
    "private/items/context_compression_fidelity_001/answer.json": "sha256:..."
  },
  "bundle_digest": "sha256:..."
}
```

校验顺序：

1. 解析 `bundle.json`，检查 schema/version/status；
2. 检查所有 entrypoint/ref 存在且在允许根目录内；
3. 校验每个文件 hash；
4. 校验 `ir_checksum`、`task_checksum`、rubric checksum；
5. 校验 `bundle_digest`；
6. 校验 Task/IR/rubric 的 ID 引用集合、ownership、private mount 和命令；
7. 输出 `BundleValidation`：`valid | degraded | rejected`，每个 warning/error 带 code 和 ref。

bundle digest 不应包含自身字段；可以对“去掉 `integrity.manifest_digest` 的 canonical
manifest + 排序后的 file hash map”计算，避免自引用。

## 9. 版本和兼容策略

- `benchmarkbundle.v2`、`taskspec.v1`、`envspec.v1`、`trialresult.v1`、
  `evaluator-contract.v1`、`projection.v1` 独立版本。
- 只增加可选字段属于 minor-compatible；删除字段、改变语义、改变可见性或改变 hash
  语义必须升主版本。
- 读取方按 schema 分支；未知主版本拒绝，已知版本但缺可选能力可以 `degraded`。
- `bundle.version` 是 benchmark 内容版本；`producer.version` 和 runtime backend
  版本不是 benchmark 版本。
- 同一个 `bundle_digest` 在不同 backend 上产生的 TrialResult 必须能放入同一历史集合。

## 10. 与现有产物的映射

| 现有产物 | v2 处理 |
|---|---|
| `benchmark.json` | 保留为 Forge generation artifact；发布时抽取到 `specs/benchmark.json` |
| `candidate.json` | 保留为 staging/governance evidence；不进入执行输入主路径 |
| `validation/environment-ir.json` | 复制/规范化为 `specs/ir.json` |
| `bundle/meta.yaml` | 生成 `runtime/octagon-env/meta.yaml` 的兼容投影，不再是语义源 |
| `bundle/tasks/*.json` | 生成 `specs/tasks/*.json`，再投影给 Octagon `tasks/*.json` |
| `core.py/mcp_server.py` | 放入 `runtime/`，由 entrypoint 明确引用 |
| `scorer.py` | 迁移为 agent-eval Evidence Adapter；过渡期标为 non-authoritative verifier |
| 旧 `evaluation.json` | 仅提供 `legacy-import.v1` 读取器；不得作为新发布格式 |
| `TrialResult` | 不放回 bundle；它是一次 execution 的输出 |

## 11. 分阶段落地

### Phase 0：冻结协议和 fixture

- 新增 `bundle.json/checksums.json/specs/ir.json` 的 schema 与 canonical hash 工具；
- 选一个现有通过 clean bundle acceptance 的环境做 golden fixture；
- validator 只读，不改变旧生成链路。

### Phase 1：Forge 发布器

新增 `publish_bundle(candidate_id) -> BundleManifest`：

- 从 candidate reload，要求 IR frozen、component link passed、static validation passed；
- 生成 TaskSpec、runtime entrypoint、evaluator contract 和 file manifest；
- 将 public/private/runtime/evaluator 分目录；
- 任何不可证明的引用或隔离问题输出 `degraded`，不发布 `released`。

### Phase 2：eval-system 消费 v2

新增 `BundleLoader`：

```python
bundle = BundleLoader(path).load()
task = bundle.task_spec(item_id)
env = bundle.environment_spec(item_id)
trial = await backend.run(task, agent, env, run_id=...)
```

`BundleLoader` 只负责校验和投影，不 import benchmark-forge，也不读取 candidate registry。
现有 `benchmark_to_task_specs(evaluation.json)` 保留为 legacy adapter。

### Phase 3：agent-octagon 兼容投影

实现 `octagon_projection.v1`：

- `runtime/` -> env directory；
- `specs/tasks/*.json` -> `tasks/*.json`；
- evaluator/private 不复制到 Agent workspace；
- 记录 projection manifest 和源 bundle digest；
- Octagon 的 `scorer.py` 只写 verifier facts。

### Phase 4：agent-eval 统一评分

新增 `TrialResult -> EvidencePackage` adapter：

- 通过 `artifact_id/evidence_id` binding 解析 evidence；
- 校验 bundle digest、IR checksum、task checksum 和 TrialResult spec refs；
- 使用冻结 rubric 重算 dimension/overall/verdict；
- 写入报告时记录 bundle digest、projection version、evaluator version。

### Phase 5：废弃旧主路径

当 v2 fixture 完成至少一条真实 `eval-system -> agent-octagon -> agent-eval` 闭环后：

- 禁止新代码生成 `evaluation.json`；
- 禁止下游直接读 `meta.yaml` 推断 rubric；
- 禁止把 runtime scorer 的 total score 作为 agent-eval 最终分数；
- 旧格式只读，不再写入。

## 12. 验收矩阵

必须至少通过：

1. 同一 bundle 在 LocalBackend 和 HarborBackend 产生字段相同的 TrialResult；
2. 篡改任意 task/runtime/rubric/private 文件时 BundleLoader 明确 checksum failure；
3. Agent-view 中不存在 expected、private、scorer implementation 和 evaluator secrets；
4. runtime 命令、module、task、artifact 路径全部可解析；
5. task instruction、IR、rubric 的引用集合无漂移；
6. attempt 前 clean workspace 不要求目标 artifact 已存在；
7. Agent 生成 artifact 后，agent-eval 能通过 logical artifact_id 找到它；
8. 同一 TrialResult 在不改变分数的情况下可脱离 Octagon 私有目录离线重评；
9. 旧 `evaluation.json` 能通过 legacy adapter 转换，但新 bundle 不依赖它；
10. bundle manifest、lock、checksums、projection 和 TrialResult 能形成完整 provenance 链。

## 13. 当前明确延后的问题

- registry:// 远程 bundle 的签名和内容寻址存储；
- 多 item 共享 runtime 镜像的 layer/cache 优化；
- 加密 private material 的密钥管理；
- 非 Octagon runtime 的具体 adapter 实现。

这些问题不改变当前的文件协议、边界、hash 和 logical-ID 设计，因此不应阻塞 v2
最小闭环。
