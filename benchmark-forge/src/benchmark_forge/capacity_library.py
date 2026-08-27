from __future__ import annotations

"""Reusable Agent Capacity Library.

The library defines *constructs and observable behaviors*, not finished quiz
questions. Each capacity is a planning primitive that Benchmark Forge can turn
into an open, executable benchmark with tools, artifacts, state, and evidence.
"""

from enum import StrEnum
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .preference_alignment import BenchmarkPlanCandidate


class CapacityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityId(StrEnum):
    INSTRUCTION_FOLLOWING = "instruction_following"
    AESTHETIC_QUALITY = "aesthetic_quality"
    SELF_TOOL_BUILDING = "self_tool_building"
    REFLECTION = "reflection"
    HALLUCINATION_CONTROL = "hallucination_control"
    LONG_HORIZON_DURABILITY = "long_horizon_durability"
    ROBUSTNESS_FAULT_TOLERANCE = "robustness_fault_tolerance"
    EFFICIENCY = "efficiency"
    CONTEXT_COMPRESSION_FIDELITY = "context_compression_fidelity"
    MEMORY_SELECTION_ACCURACY = "memory_selection_accuracy"
    AUTONOMOUS_TERMINATION_SELF_EVALUATION = "autonomous_termination_self_evaluation"
    DELEGATION_QUALITY = "delegation_quality"


class EvidenceSource(StrEnum):
    TOOL_TRACE = "tool_trace"
    STATE_TRANSITION = "state_transition"
    ARTIFACT = "artifact"
    FINAL_RESPONSE = "final_response"
    RESOURCE_USAGE = "resource_usage"
    EXTERNAL_CHECK = "external_check"
    HUMAN_PREFERENCE = "human_preference"


class CapacityDefinition(CapacityModel):
    capability_id: CapabilityId
    name_zh: str
    name_en: str
    description: str
    construct_definition: str
    observable_behaviors: list[str] = Field(min_length=2)
    anti_patterns: list[str] = Field(min_length=1)
    recommended_task_forms: list[Literal["executable_task", "hybrid", "static_question"]] = Field(
        default_factory=lambda: ["executable_task"]
    )
    required_environment_features: list[str] = Field(default_factory=list)
    required_agent_capabilities: list[str] = Field(default_factory=list)
    evidence_sources: list[EvidenceSource] = Field(min_length=1)
    scoring_dimensions: list[str] = Field(min_length=2)
    perturbations: list[str] = Field(default_factory=list)
    prerequisite_capabilities: list[CapabilityId] = Field(default_factory=list)
    human_preference_relevance: Literal["low", "medium", "high"] = "medium"
    default_difficulty: Literal["moderate", "hard", "very_hard"] = "hard"
    version: str = "1"
    status: Literal["active", "draft", "deprecated"] = "active"

    @field_validator("observable_behaviors", "scoring_dimensions")
    @classmethod
    def nonempty_strings(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("capacity library entries must contain non-empty strings")
        return values


class CapacityBenchmarkSpec(CapacityModel):
    capability_id: CapabilityId
    benchmark_intent: str
    task_form: Literal["executable_task", "hybrid", "static_question"] = "executable_task"
    required_tools: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    required_observations: list[str] = Field(default_factory=list)
    scoring_intent: list[str] = Field(min_length=2)
    perturbation_plan: list[str] = Field(default_factory=list)
    difficulty_notes: str
    plan_only: bool = True

    def to_plan_candidate(self, *, plan_id: str, goal: str) -> BenchmarkPlanCandidate:
        definition = DEFAULT_CAPACITY_LIBRARY.get(self.capability_id)
        return BenchmarkPlanCandidate(
            plan_id=plan_id,
            title=f"{definition.name_zh} benchmark plan",
            capability=self.capability_id.value,
            task_form=self.task_form,
            task_description=f"{goal}\n\n{self.benchmark_intent}",
            environment_description="; ".join(self.required_tools) or "observable executable environment",
            behavior_requirements=definition.observable_behaviors,
            artifact_requirements=self.required_artifacts,
            scoring_intent=self.scoring_intent,
            difficulty_intent=self.difficulty_notes,
            cost_intent="measure quality and resource cost without collapsing to a single score",
            provenance={
                "capacity_id": self.capability_id.value,
                "capacity_version": definition.version,
                "plan_only": self.plan_only,
            },
        )


class AgentCapacityLibrary(CapacityModel):
    version: str = "agent-capabilities.v1"
    definitions: list[CapacityDefinition]

    @model_validator(mode="after")
    def unique_ids(self) -> "AgentCapacityLibrary":
        ids = [item.capability_id for item in self.definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("capacity ids must be unique")
        return self

    def get(self, capability_id: CapabilityId | str) -> CapacityDefinition:
        key = CapabilityId(capability_id)
        for definition in self.definitions:
            if definition.capability_id == key:
                return definition
        raise KeyError(f"unknown agent capability: {key.value}")

    def search(self, text: str) -> list[CapacityDefinition]:
        tokens = {token.lower() for token in text.replace("/", " ").split() if token.strip()}
        if not tokens:
            return list(self.definitions)
        results: list[CapacityDefinition] = []
        for definition in self.definitions:
            haystack = " ".join([
                definition.capability_id.value,
                definition.name_zh,
                definition.name_en,
                definition.description,
                definition.construct_definition,
                *definition.observable_behaviors,
            ]).lower()
            if any(token in haystack for token in tokens):
                results.append(definition)
        return results

    def benchmark_spec(self, capability_id: CapabilityId | str, *, intent: str | None = None) -> CapacityBenchmarkSpec:
        definition = self.get(capability_id)
        return CapacityBenchmarkSpec(
            capability_id=definition.capability_id,
            benchmark_intent=intent or definition.construct_definition,
            task_form=definition.recommended_task_forms[0],
            required_tools=definition.required_environment_features,
            required_artifacts=[f"evidence/{definition.capability_id.value}.json"],
            required_observations=[source.value for source in definition.evidence_sources],
            scoring_intent=definition.scoring_dimensions,
            perturbation_plan=definition.perturbations,
            difficulty_notes=(
                f"{definition.default_difficulty}; avoid static multiple-choice compression; "
                "require observable behavior and at least one recovery/verification path"
            ),
        )

    def model_dump_json(self, **kwargs):  # type: ignore[override]
        return super().model_dump_json(**kwargs)


def _definition(
    capability_id: CapabilityId,
    name_zh: str,
    name_en: str,
    description: str,
    construct: str,
    behaviors: list[str],
    anti_patterns: list[str],
    features: list[str],
    evidence: list[EvidenceSource],
    scoring: list[str],
    perturbations: list[str],
    *,
    agent_capabilities: list[str] | None = None,
    preference: Literal["low", "medium", "high"] = "medium",
    difficulty: Literal["moderate", "hard", "very_hard"] = "hard",
) -> CapacityDefinition:
    return CapacityDefinition(
        capability_id=capability_id,
        name_zh=name_zh,
        name_en=name_en,
        description=description,
        construct_definition=construct,
        observable_behaviors=behaviors,
        anti_patterns=anti_patterns,
        required_environment_features=features,
        required_agent_capabilities=agent_capabilities or [],
        evidence_sources=evidence,
        scoring_dimensions=scoring,
        perturbations=perturbations,
        human_preference_relevance=preference,
        default_difficulty=difficulty,
    )


def build_default_capacity_library() -> AgentCapacityLibrary:
    return AgentCapacityLibrary(definitions=[
        _definition(
            CapabilityId.INSTRUCTION_FOLLOWING, "指令遵从", "instruction following",
            "在多约束、冲突优先级和格式要求下，准确完成用户意图。",
            "follow explicit and implicit constraints while preserving task intent",
            ["识别约束层级", "处理冲突指令", "保持指定输出协议", "在不确定时澄清或安全降级"],
            ["只完成最容易的子任务", "忽略负面约束", "格式正确但内容偏题"],
            ["constraint-bearing task", "structured output validator"],
            [EvidenceSource.TOOL_TRACE, EvidenceSource.STATE_TRANSITION, EvidenceSource.FINAL_RESPONSE],
            ["constraint satisfaction", "intent coverage", "format validity", "unjustified deviation"],
            ["conflicting instructions", "irrelevant distractor", "late constraint injection"],
        ),
        _definition(
            CapabilityId.AESTHETIC_QUALITY, "美学能力", "aesthetic quality",
            "在开放创作任务中形成连贯、合目的、可解释且经过迭代改进的作品。",
            "produce and refine artifacts according to explicit and latent quality criteria",
            ["形成整体风格", "保持结构与细节一致", "根据反馈迭代", "解释设计取舍"],
            ["堆砌装饰", "局部精美但整体失衡", "只迎合表面关键词", "无法根据反馈修正"],
            ["artifact workspace", "render/preview tool", "revision feedback"],
            [EvidenceSource.ARTIFACT, EvidenceSource.TOOL_TRACE, EvidenceSource.FINAL_RESPONSE, EvidenceSource.HUMAN_PREFERENCE],
            ["coherence", "fitness to purpose", "craft/detail", "revision quality", "human preference alignment"],
            ["style constraint", "audience change", "ambiguous brief", "adversarial aesthetic distractor"],
            preference="high", difficulty="very_hard",
        ),
        _definition(
            CapabilityId.SELF_TOOL_BUILDING, "自制工具能力", "self-tool building",
            "发现重复或能力缺口，设计、实现、测试并使用自己的工具完成任务。",
            "create fit-for-purpose tools when existing interfaces are insufficient",
            ["识别工具缺口", "设计最小接口", "实现并测试工具", "在真实任务中正确使用"],
            ["为了炫技造工具", "工具不可复现", "不测试就使用", "工具输出未经验证"],
            ["sandboxed code execution", "tool registry", "test harness", "artifact filesystem"],
            [EvidenceSource.TOOL_TRACE, EvidenceSource.ARTIFACT, EvidenceSource.STATE_TRANSITION, EvidenceSource.EXTERNAL_CHECK],
            ["problem diagnosis", "tool usefulness", "test coverage", "integration correctness", "security boundary"],
            ["API failure", "dependency restriction", "tool output mismatch", "limited budget"],
            difficulty="very_hard",
        ),
        _definition(
            CapabilityId.REFLECTION, "反思能力", "reflection",
            "基于轨迹、反馈和结果识别自身错误，并形成可执行的修正策略。",
            "inspect evidence of own behavior and convert errors into corrective action",
            ["定位错误步骤", "区分原因与症状", "提出可验证修复", "复盘后改变后续行为"],
            ["泛泛道歉", "重复相同策略", "把失败归因给环境", "反思与证据无关"],
            ["trajectory log", "checkpoint", "failure injection", "replay tool"],
            [EvidenceSource.TOOL_TRACE, EvidenceSource.STATE_TRANSITION, EvidenceSource.FINAL_RESPONSE],
            ["error localization", "causal accuracy", "repair effectiveness", "behavior change"],
            ["misleading error", "partial success", "conflicting feedback", "delayed consequence"],
        ),
        _definition(
            CapabilityId.HALLUCINATION_CONTROL, "幻觉控制", "hallucination control",
            "在证据不足、来源冲突或工具不可用时控制无依据断言。",
            "maintain calibrated claims and abstain or verify when evidence is insufficient",
            ["区分已知与推断", "检查来源", "主动验证", "在无法验证时明确限制"],
            ["编造引用", "把可能说成确定", "引用与结论不匹配", "工具失败后继续假装已验证"],
            ["evidence store", "citation checker", "tool failure injection"],
            [EvidenceSource.TOOL_TRACE, EvidenceSource.EXTERNAL_CHECK, EvidenceSource.FINAL_RESPONSE],
            ["claim support", "calibration", "abstention quality", "citation fidelity"],
            ["missing evidence", "source conflict", "plausible distractor", "stale source"],
            preference="high",
        ),
        _definition(
            CapabilityId.LONG_HORIZON_DURABILITY, "长任务耐久力", "long-horizon durability",
            "在长时间、多阶段、有状态任务中保持目标、计划和执行质量。",
            "sustain coherent progress over long horizons without silent drift",
            ["维护长期目标", "分阶段检查点", "恢复中断状态", "控制累积错误"],
            ["目标漂移", "后期质量崩溃", "重复劳动", "忘记已完成约束"],
            ["persistent workspace", "checkpoint/resume", "long task queue", "progress ledger"],
            [EvidenceSource.TOOL_TRACE, EvidenceSource.STATE_TRANSITION, EvidenceSource.ARTIFACT, EvidenceSource.RESOURCE_USAGE],
            ["goal retention", "progress continuity", "recovery quality", "late-stage quality", "wasted work"],
            ["context truncation", "interruption", "new dependency", "partial tool outage"],
            difficulty="very_hard",
        ),
        _definition(
            CapabilityId.ROBUSTNESS_FAULT_TOLERANCE, "鲁棒性/容错", "robustness and fault tolerance",
            "面对工具、环境、输入和协作者异常时保持可恢复、可解释的执行。",
            "degrade safely and recover from foreseeable faults without corrupting state",
            ["检测故障", "重试与替代路径", "隔离坏状态", "保留可恢复进度"],
            ["无限重试", "吞掉错误", "覆盖正确状态", "故障后编造成功"],
            ["fault injection", "transactional state", "retry budget", "fallback tools"],
            [EvidenceSource.TOOL_TRACE, EvidenceSource.STATE_TRANSITION, EvidenceSource.EXTERNAL_CHECK],
            ["fault detection", "recovery success", "state integrity", "retry discipline"],
            ["timeout", "malformed output", "partial write", "tool disagreement", "resource exhaustion"],
        ),
        _definition(
            CapabilityId.EFFICIENCY, "效率", "efficiency",
            "在质量约束下合理使用时间、工具调用、上下文和计算资源。",
            "optimize task execution cost without sacrificing required quality",
            ["选择合适工具", "减少重复调用", "并行独立工作", "在预算内达到质量阈值"],
            ["过早优化", "省调用导致漏证据", "无意义并行", "质量换成本不透明"],
            ["resource meter", "time/tool budget", "parallel tools", "quality oracle"],
            [EvidenceSource.RESOURCE_USAGE, EvidenceSource.TOOL_TRACE, EvidenceSource.EXTERNAL_CHECK],
            ["quality-adjusted cost", "time-to-valid-result", "tool efficiency", "budget compliance"],
            ["tight budget", "variable latency", "parallel opportunity", "costly verification"],
        ),
        _definition(
            CapabilityId.CONTEXT_COMPRESSION_FIDELITY, "上下文压缩保真度", "context compression fidelity",
            "压缩长上下文时保留后续决策所需的事实、约束、关系和不确定性。",
            "compress context while preserving decision-relevant information and provenance",
            ["识别关键信息", "保留约束关系", "标注不确定性", "压缩后正确恢复任务状态"],
            ["只保留主题词", "丢失否定条件", "混淆来源", "压缩后产生新事实"],
            ["controlled long-document source", "late-fact source", "provenance oracle", "host context-reset trigger/observer"],
            [EvidenceSource.STATE_TRANSITION, EvidenceSource.FINAL_RESPONSE, EvidenceSource.EXTERNAL_CHECK],
            ["fact recall", "constraint recall", "relation fidelity", "uncertainty preservation"],
            ["distractor density", "contradictory documents", "late retrieval", "compression budget"],
            agent_capabilities=["context_management", "memory", "workspace"], preference="high",
        ),
        _definition(
            CapabilityId.MEMORY_SELECTION_ACCURACY, "记忆选择准确性", "memory selection accuracy",
            "从大量历史记忆中选择与当前任务真正相关的内容，避免无关或污染记忆。",
            "retrieve, prioritize, and suppress memories according to current task relevance",
            ["判断相关性", "区分近期与权威", "拒绝污染记忆", "在缺失时承认不知道"],
            ["关键词命中即采用", "被旧错误记忆绑架", "记忆越多越好", "无来源复述"],
            ["memory store", "retrieval/filter tool", "distractor memories", "source metadata"],
            [EvidenceSource.TOOL_TRACE, EvidenceSource.STATE_TRANSITION, EvidenceSource.EXTERNAL_CHECK],
            ["relevance precision", "relevance recall", "source weighting", "contamination resistance"],
            ["stale memory", "near-duplicate distractor", "conflicting authority", "missing memory"],
        ),
        _definition(
            CapabilityId.AUTONOMOUS_TERMINATION_SELF_EVALUATION, "自主终止与自我验收", "autonomous termination and self-evaluation",
            "判断任务是否达到验收标准，在完成、失败或需要继续之间作出正确终止决策。",
            "stop at the right time based on explicit acceptance evidence and unresolved risk",
            ["读取验收标准", "执行自检", "识别未完成项", "在达到标准后终止"],
            ["无限循环", "过早宣布完成", "只检查形式", "把自我报告当证据"],
            ["acceptance checklist", "objective verifier", "progress state", "failure budget"],
            [EvidenceSource.STATE_TRANSITION, EvidenceSource.TOOL_TRACE, EvidenceSource.EXTERNAL_CHECK, EvidenceSource.FINAL_RESPONSE],
            ["completion correctness", "false-stop rate", "unnecessary-work rate", "self-check validity"],
            ["ambiguous completion", "hidden missing requirement", "flaky verifier", "near-success"],
            preference="high",
        ),
        _definition(
            CapabilityId.DELEGATION_QUALITY, "任务委派质量", "delegation quality",
            "将复杂任务拆解为合适的子任务，明确指派、边界、依赖、验收和整合方式。",
            "delegate work so independent agents can execute safely and their outputs can be verified and integrated",
            ["识别可并行部分", "定义输入输出契约", "分配最小必要上下文", "验收并修复子任务", "整合冲突结果"],
            ["机械拆分", "重复或冲突指派", "共享写权限失控", "只收集不验收", "把 subagent 自报当事实"],
            ["controlled case materials", "evaluation-system failure/conflict injector", "artifact registry", "independent acceptance verifier"],
            [EvidenceSource.TOOL_TRACE, EvidenceSource.STATE_TRANSITION, EvidenceSource.ARTIFACT, EvidenceSource.EXTERNAL_CHECK, EvidenceSource.HUMAN_PREFERENCE],
            ["decomposition quality", "assignment clarity", "parallelism", "acceptance quality", "integration correctness", "scope safety"],
            ["dependency inversion", "subagent failure", "conflicting outputs", "missing artifact", "partial completion"],
            agent_capabilities=["subagent_spawn", "subagent_message", "subagent_wait", "subagent_trace", "workspace"],
            preference="high", difficulty="very_hard",
        ),
    ])


DEFAULT_CAPACITY_LIBRARY = build_default_capacity_library()
