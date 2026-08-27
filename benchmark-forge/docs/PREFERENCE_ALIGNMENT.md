# Benchmark Forge Preference Alignment 设计

状态：`design-frozen-before-implementation`

冻结日期：2026-08-24

本文定义 Benchmark Forge 如何在规划完成、环境组装之前使用有限人类 preference。
跨项目边界以：

- `../../docs/PREFERENCE_SYSTEM_ARCHITECTURE.md`
- `../../docs/PREFERENCE_DATA_GOVERNANCE.md`
- `../../docs/PREFERENCE_CONTRACTS.md`

为准。

## 1. 角色位置

Benchmark Forge 在原有角色之外增加：

```text
Preference Alignment
```

完整规划到实现流程：

```text
Design
  ↓
Grounding
  ↓
Allocation
  ↓
Planning Branch A ──┐
                    ├─ BenchmarkPlan Pair
Planning Branch B ──┘
  ↓
Preference Alignment
  ↓
Selected BenchmarkPlan
  ↓
Executor materialization
  ↓
Verification & Control
```

Preference Alignment 不替代 Verification & Control：

```text
Preference Alignment
  = 在实现前预测人类偏好、选择规划

Verification & Control
  = 检查 contract、环境、scorer、证据和执行质量
```

## 2. 同 prompt 双规划

第一版明确使用最简单策略：

```text
同一模型
同一 prompt
同一 UserGoal
同一 Benchmark state snapshot
同一 knowledge snapshot
同一资源约束
两个相互隔离的模型调用
```

不设计 A/B 专属 prompt，不要求不同角色人格，不通过复杂 prompt 制造差异。

必须保证：

- 无共享 message history；
- A 不可读取 B；
- B 不可读取 A；
- response cache 不复用同一输出；
- 每次调用记录 model/provider/prompt/knowledge checksum；
- 两个 Plan 都先通过相同 deterministic schema validation。

### 差异不足

如果两个 Plan：

- checksum 完全相同；或
- 结构相似度高于配置阈值；

则使用**同一个 prompt**进行最多一次额外采样。仍不生成新 prompt。

```python
class DoublePlanningPolicy:
    samples = 2
    max_resamples_on_duplicate = 1
    duplicate_similarity_threshold = 0.95
```

若额外采样后仍无有意义差异：

```text
preference_status=insufficient_diversity
```

不得浪费人类 preference，也不得伪造 A/B 差异。可根据 policy：

- 选择通过确定性质量检查的任一等价 Plan；
- 或回到 Design 修订；
- 或记录无需 preference。

## 3. BenchmarkPlanCandidate

Preference 比较对象是规划，不是 bundle。

```python
class BenchmarkPlanCandidate:
    plan_id: str
    branch_id: str

    user_goal_ref: str
    benchmark_state_ref: str
    knowledge_snapshot_ref: str

    target_capabilities: list[str]
    capability_boundaries: list[str]

    scenario: str
    task_concept: str
    public_instruction_draft: str

    environment_requirements: dict
    tool_requirements: list[dict]
    material_plan: list[dict]

    required_agent_behaviors: list[str]
    acceptable_solution_variations: list[str]
    forbidden_shortcuts: list[str]

    artifact_contracts: list[dict]
    scoring_intent: list[dict]
    evidence_requirements: list[str]

    intended_difficulty_sources: list[str]
    accidental_difficulty_risks: list[str]

    estimated_implementation_cost: dict
    unresolved_questions: list[str]

    provenance: dict
```

Plan 不包含：

- 完整 `core.py/scorer.py`；
- private expected answer；
- hidden verifier；
- 凭据；
- host path；
- 已宣称的 runtime success。

## 4. BenchmarkComparisonSchema

Benchmark 的结构化问题由 Benchmark Forge 拥有并版本化，Preference Arena 只渲染。

MVP criterion：

### target_alignment

```text
哪个规划更准确、完整地覆盖用户要求评测的能力，并且能力边界更清楚？
```

相关结构：

```text
target_capabilities
capability_boundaries
required_agent_behaviors
```

### task_structure

```text
哪个规划的任务结构更自然地迫使 Agent 展示目标能力，而不是只产出最终文件？
```

相关结构：

```text
scenario
task_concept
public_instruction_draft
```

### solution_openness

```text
哪个规划允许更多合理解决方式，同时不依赖唯一内部实现或固定 DAG？
```

相关结构：

```text
acceptable_solution_variations
forbidden_shortcuts
```

### environment_support

```text
哪个规划的环境、工具和材料更能真实支撑任务，且较少引入无关执行障碍？
```

相关结构：

```text
environment_requirements
tool_requirements
material_plan
```

### behavior_observability

```text
哪个规划更容易从 runtime、state、artifact 或 verifier 观察到目标行为是否真实发生？
```

相关结构：

```text
required_agent_behaviors
artifact_contracts
evidence_requirements
```

### scoring_validity

```text
哪个规划的评分意图更能测量目标能力，而不是依赖隐藏条件、表面字符串或 Agent 自报？
```

相关结构：

```text
scoring_intent
evidence_requirements
forbidden_shortcuts
```

### difficulty_quality

```text
哪个规划的难度主要来自目标能力，而不是环境故障、材料缺失、任务歧义或不合理 timeout？
```

相关结构：

```text
intended_difficulty_sources
accidental_difficulty_risks
```

### materialization_readiness

```text
哪个规划在不牺牲目标能力的前提下，更适合进入具体环境组装？
```

相关结构：

```text
environment_requirements
estimated_implementation_cost
unresolved_questions
```

通用 choices：

```text
left
right
tie
both_bad
not_enough_information
```

不要求人类填写 0–100 绝对分。

## 5. Preference Alignment Agent

### 5.1 角色抽象

```python
class PreferenceAlignmentAgent(Protocol):
    def compare(
        goal: UserGoal,
        plan_a: BenchmarkPlanCandidate,
        plan_b: BenchmarkPlanCandidate,
        schema: BenchmarkComparisonSchema,
        evidence: PreferenceEvidenceContext,
    ) -> PreferenceAlignmentDecision: ...
```

### 5.2 输入

- UserGoal；
- Benchmark state snapshot；
- Plan A/B；
- BenchmarkComparisonSchema；
- Registry 返回的相关 raw preference refs/excerpts；
- reviewed summaries；
- draft digest；
- coverage/conflicts/staleness；
- current policy thresholds。

### 5.3 输出

```python
class CriterionPreferencePrediction:
    criterion_id: str
    predicted_choice: left | right | tie | both_bad | not_enough_information
    confidence: float
    supporting_preference_refs: list[str]
    applied_principles: list[str]
    concerns: list[str]

class PlanPreferenceAssessment:
    plan_id: str
    criterion_scores: dict[str, float]
    human_alignment_estimate: float
    uncertainty: float
    strengths: list[str]
    risks: list[str]

class PreferenceAlignmentDecision:
    choice: prefer_a | prefer_b | tie | both_bad | abstain
    plan_assessments: list[PlanPreferenceAssessment]
    criterion_predictions: list[CriterionPreferencePrediction]
    confidence: float
    control_action: select | select_with_warnings | revise | regenerate | abstain
    selected_plan_id: str | None
    comparison_request_ref: str | None
    evidence_context_ref: str
    rationale: str
```

单一 overall score 只允许作为摘要，不能代替 criterion predictions。

### 5.4 允许行为

- 检索 Preference Registry；
- 比较 Plan；
- 预测分 criterion 人类偏好；
- 选择 A/B；
- tie 时按 policy 选择或 abstain；不得请求人类；
- both_bad 时要求重新规划；
- 低置信度/OOD/冲突时 abstain，并记录原因；是否进入人类离线采样由独立 scheduler/operator 决定；
- 输出明确 evidence refs 和 uncertainty。

### 5.5 禁止行为

- 修改 Plan 内容；
- 合并 A/B 成未经重新验证的 Plan；
- 直接生成 environment bundle；
- 写入 raw human preference；
- 把 draft digest 称为专家确认；
- 缺乏证据时输出伪高置信度；
- 用历史偏好强制所有 benchmark 变成同一任务形式；
- 读取 reviewer 真实身份。

## 6. Preference Registry Client

Benchmark Forge 通过接口：

```python
class PreferenceRegistryClient(Protocol):
    def submit_comparison(request) -> ComparisonRequestReceipt: ...
    def search_evidence(query) -> PreferenceEvidenceContext: ...
    def get_comparison_status(request_id) -> ComparisonStatus: ...
    def get_human_result(request_id) -> HumanPreferenceResult | None: ...
    def report_prediction_telemetry(event) -> None: ...
```

禁止读取 Registry DB/volume。

Forge 本地只保存：

```text
request ref/checksum
used evidence context ref/version
prediction decision
selected plan
human result ref（如有）
```

不缓存完整 Preference Library。

## 7. 人类路由策略

不是每个 Plan Pair 都进入 Arena。HumanSamplingPolicy 至少考虑：

```text
随机 audit sample
Preference Agent confidence
Plan score margin
both_bad prediction
not_enough_information prediction
新 capability/OOD
reviewed summary coverage
raw preference conflicts
Preference Agent 与 deterministic checks 冲突
```

人类请求属于异步证据采集。Policy 决定当前 run：

- 等待人类；
- 先使用 proxy 选择并标记 audit pending；
- 保留 draft 不 materialize。

MVP 默认 canonical promotion 前必须解决关键 audit，但不要求所有 generation 同步等待人类。

## 8. 选择与 materialization

只有一个 selected Plan 可以进入 Executor materialization。

```text
Plan A/B
  → PreferenceAlignmentDecision
  → selected_plan_id
  → ExecutableTaskContract
  → EnvironmentScaffoldBundle
```

如果 `both_bad/regenerate`：禁止 materialize。

如果 `select_with_warnings`：warning、evidence ref 和不确定性必须写入 Benchmark event/
manifest/candidate provenance。

## 9. Preference 与质量验证的关系

Preference 不等于正确性：

```text
人类喜欢一个规划
≠ contract 可实现
≠ scorer 正确
≠ environment 可运行
≠ benchmark 有区分度
```

因此 selected Plan 仍必须经过：

```text
contract validation
materialization
scorer semantic review
smoke
pilot
agent-eval
promotion policy
```

Preference 只改善实现前的规划选择。

## 10. 事件与可审计性

Benchmark event store 至少记录：

```text
planning_branch_started/completed
plan_similarity_checked
plan_resampled
comparison_request_submitted
preference_evidence_retrieved
preference_alignment_decided
offline_preference_batch_opened/closed
plan_selected
preference_alignment_failed/degraded
```

每个事件含 ref/checksum/model/prompt/knowledge/evidence version。

## 11. 失败与降级

### Registry unavailable

```text
preference_status=pending|unavailable
```

不得伪造已对齐。按用户/RunConfig：

- 保存 draft；
- 使用无 preference baseline 选择并警告；
- 或停止 materialization。

### Evidence insufficient

Agent 只能输出 abstain；不得输出请求人类介入。不得用 general prior 冒充人类库。

### Two plans both bad

回到规划阶段，用同一 prompt 重新生成新的 pair，或由 Design 修订 Benchmark state。
不得让 Preference Agent 自行拼接。

### Offline human result arrives after proxy selection

保存 calibration event；若人类与 proxy 冲突：

- 未 materialize：允许重新选择；
- 已 materialize：标记 audit conflict，不静默覆盖历史 decision；
- canonical promotion 前根据 policy 处理。

## 12. 评测 Preference Alignment Agent

必须保留 human holdout，不进入其检索上下文。指标：

```text
criterion agreement
pairwise overall agreement
confidence calibration
abstention quality
both_bad recall
not-enough-information behavior
selection uplift
human conflict rate
downstream benchmark quality uplift
diversity preservation
```

必须对比：

```text
无 Preference Alignment
vs
有 Preference Alignment
```

不能仅用 Agent 自己生成的 prediction 评测自己。

## 13. Phase 1 implementation status

已实现最小 typed runtime contract：

```text
PreferenceEvidenceQuery
  → RegistryEvidenceHttpClient
  → PreferenceEvidenceContext
  → PreferenceAlignmentAgent (PydanticAI)
  → PreferenceAlignmentDecision
```

当前实现的硬约束：

- `PreferenceAlignmentDecision.control_action` 只允许：
  `select`、`select_with_warnings`、`revise`、`regenerate`、`abstain`；
- `request_human` 不是合法 action；
- evidence 不可用时只能生成 `abstain` fallback；
- `select/select_with_warnings` 必须拥有非 stale 的 approved summary evidence；
- Forge 只通过 `/v1/evidence/search` 查询 Registry，不读取 DB/volume；
- runtime alignment 不创建人类 assignment；人类采样由离线 scheduler/operator 负责。

当前仍未将该角色接入主 materialization orchestrator。接入前必须补充：

1. `BenchmarkPlanCandidate` 从 Design/双规划输出的 adapter；
2. 双规划和有界重采样状态；
3. selected Plan 到 Executor 的显式 gate；
4. decision/evidence provenance 写入 Benchmark event store。

## 14. Double planning and materialization gate implementation

新增：

```text
benchmark_forge.planning_pair
```

核心流程：

```text
same prompt checksum
  ├─ independent call → Plan A
  └─ independent call → Plan B
          ↓
    similarity check
          ↓
    same-prompt bounded resample
          ↓
    BenchmarkPlanPair
          ↓
    PreferenceAlignmentDecision
          ↓
    MaterializationGate
```

`DoublePlanningService` 的约束：

- 两次调用使用完全相同的 prompt；
- model id 和 knowledge snapshot 必须一致；
- branch 不共享 conversation history；
- 差异不足时只重新调用相同 prompt；
- 达到 resample 上限后标记 `insufficient_diversity`；
- 不生成 A/B 专属差异 prompt；
- 不因为差异不足而自动请求人类。

`MaterializationGate` 的约束：

- 只有 `select` 或 `select_with_warnings` 可以通过；
- `revise`、`regenerate`、`abstain` 均阻止 materialization；
- selected plan 必须属于当前 pair；
- `insufficient_diversity` pair 不得 materialize。

## 15. Composable pipeline

新增 `PlanningAlignmentPipeline`，可在不改变旧五角色 orchestrator 的情况下运行：

```text
DoublePlanningService
  → BenchmarkPlanPair
  → PreferenceEvidenceContext
  → PreferenceAlignmentDecision
  → MaterializationGate
```

默认安全行为：

- evidence client 不可用且没有 decider：输出 `abstain`；
- pair diversity 不足：输出 `regenerate`；
- 只有 `select/select_with_warnings` 能返回 `selected_plan`；
- 其他 action 的 `selected_plan` 始终为 `None`；
- 人类离线采样不会从 pipeline 内部触发。

## 16. BenchmarkGenerationService 接入状态

`BenchmarkGenerationService` 新增 opt-in 入口：

```python
service.generate_with_alignment(...)
```

其流程为：

```text
同一 goal/prompt
  ├─ BenchmarkOrchestrator branch 1
  └─ BenchmarkOrchestrator branch 2
          ↓
  benchmark_to_plan adapter
          ↓
  PlanningAlignmentPipeline
          ↓
  MaterializationGate
          ↓
  仅 selected branch 进入 environment-candidates/materialization
```

安全行为：

- `select/select_with_warnings`：返回 selected Benchmark，并允许后续 materialization；
- `abstain`：返回 `benchmark=None`，不 materialize；
- `regenerate`：返回 `benchmark=None`，不 materialize；
- `revise`：返回 `benchmark=None`，不 materialize；
- 旧 `generate()` 入口仍保持兼容，不自动启用人类对齐；
- 只有显式调用 `generate_with_alignment()` 才会进入新流程。
