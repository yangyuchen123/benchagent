# Benchmark Forge Preference Alignment 验收标准

状态：`design-frozen-before-implementation`

## Double Planning

- [ ] 同一 prompt/model/state/knowledge snapshot 独立调用两次；
- [ ] 两个 branch 无共享 message history；
- [ ] response cache 不复用；
- [ ] prompt/model/knowledge checksum 被记录；
- [ ] 两个 Plan 使用相同 schema validator；
- [ ] 差异不足只用同 prompt 有界重采样，不生成复杂差异 prompt；
- [ ] 仍无差异时标记 insufficient_diversity，不送人工虚假比较。

## Plan Contract

- [ ] Preference 对象是 BenchmarkPlanCandidate，不是完整 bundle；
- [ ] Plan 不包含 credentials/private expected/hidden scorer；
- [ ] Plan 包含 capability、task、environment、behavior、artifact、scoring intent、difficulty 和 cost；
- [ ] Plan 有 branch/provenance/checksum。

## Comparison Schema

- [ ] Benchmark 专属问题由 Forge 拥有；
- [ ] Arena 不内置 Benchmark criterion；
- [ ] criterion 覆盖 target/task/openness/environment/observability/scoring/difficulty/readiness；
- [ ] 使用 pairwise choices，不要求人类绝对打分；
- [ ] schema 版本化且 request 保存 checksum。

## Preference Alignment Role

- [x] 输出 criterion predictions 和 uncertainty；
- [x] 输出 evidence refs；
- [x] 支持 select/revise/regenerate/abstain；
- [ ] 不存在 `request_human` action；Agent 不创建 assignment、不阻塞等待 reviewer；
- [ ] both_bad 不 materialize；
- [ ] 不修改/自由合并 Plan；
- [x] 不写 human preference；
- [x] draft digest 被标为低 authority；
- [x] evidence 不足可 abstain；
- [x] `select` 必须有非 stale approved summary evidence；

## Registry Integration

- [x] 只通过 API/JSON contract；
- [x] 不挂载 Registry DB/volume；
- [ ] Forge 只保存 refs/checksums/decision；
- [ ] Registry unavailable 时明确 degraded/pending；
- [ ] schema incompatibility 不被静默忽略。

## Offline Human Sampling

- [ ] 人类对齐只由离线 scheduler/operator 批处理启动；
- [ ] 运行中的 Forge Agent 不得主动提出、创建或等待人类介入；


- [ ] 不要求每个 benchmark 人工审核；
- [ ] 支持由离线 scheduler 进行随机 audit、低置信度、OOD、conflict 和 both_bad 采样；Agent 不产生人工介入请求；
- [ ] human result 可用于 calibration；
- [ ] proxy/human 冲突保留历史，不静默覆盖；
- [ ] holdout preference 不进入 Agent retrieval。

## Materialization

- [ ] 只有一个 selected Plan 进入 materialization；
- [ ] select_with_warnings 写入 event/manifest/provenance；
- [ ] Preference 不绕过 scorer/smoke/pilot/agent-eval；
- [ ] 人类偏好不被解释为可执行性证明。

## Evaluation

- [ ] 有 preference on/off 对照；
- [ ] 计算 human agreement 和 calibration；
- [ ] 检查 downstream quality uplift；
- [ ] 检查 diversity collapse；
- [ ] 不用 Agent prediction 作为唯一 ground truth。

## Double Planning and Gate

- [x] 同 prompt checksum 生成两个独立 Plan；
- [x] model/knowledge snapshot provenance 一致；
- [x] 差异不足只做同 prompt 有界重采样；
- [x] 超过上限标记 `insufficient_diversity`；
- [x] 未被 `select/select_with_warnings` 选中的 Plan 不得 materialize；
- [x] `abstain/revise/regenerate` 阻止 materialization。
