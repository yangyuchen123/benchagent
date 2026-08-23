"""Prompt templates for all agents.

Prompts are derived from the component descriptions in the paper (Sec. 3):
Design Agent (Propose/Revise/Discard), Grounding Agent (Preference/Search/
Transformability/Score-and-Filter), Allocation Agent (Allocate/Diagnose/Adjust),
and the Executor (orchestration, verification).
"""
from __future__ import annotations

import json
from typing import Any

DESIGN_PROPOSE_SYSTEM = """\
You are the Design Agent of an autonomous benchmark construction system.
Your job: translate a high-level evaluation goal written in natural language into a \
small, coherent set of structured SUBTASKS. Each subtask must:
- capture ONE atomic, independently testable evaluation dimension;
- differ in granularity, coverage or emphasis from the others, together covering the \
user's intent from complementary angles;
- specify input modalities (text / image / audio / mixed), an answer type, and an \
expected output schema.

You will later be validated against real datasets, so avoid impossible or self-contained \
requirements. Aim for 2-4 subtasks.

Respond with STRICT JSON only, in this schema:
{"subtasks": [{"id": "snake_case_id", "name": "Short name",
  "description": "What capability it evaluates and how",
  "modalities": ["text"|"image"|"audio"|...],
  "answer_type": "multiple_choice"|"open_ended"|"true_false",
  "output_schema": {"question": "...", "options": ["..."], "answer": "..."}}]}
"""

DESIGN_REVISE_SYSTEM = """\
You are the Design Agent. The Grounding Agent rejected the current subtask set because \
at least one subtask has no feasible (dataset, transformation) realization. \
Revise the subtasks so that every one can be realized on real data using available \
transformation tools.

Rules:
- You MAY output FEWER subtasks than before (even 1-2). Quality over quantity.
- Preserve the core evaluation intent; adjust granularity, scope or modality so the \
subtasks become groundable on the given datasets.
- If some subtasks were already successfully grounded, KEEP them unchanged (same id, \
name, description, modalities, answer_type) and only revise or drop the failing ones.
- Keep each subtask atomic and directly realizable (e.g. reading/comparison/reasoning \
over the given source material) rather than requiring content that does not exist.
"""

GROUNDING_PREFERENCE_SYSTEM = """\
You are the Grounding Agent. For a given subtask, first characterize what kind of source \
data could support it: required modalities, annotation structure, domain, and content \
properties. This preference description will be used to score candidate datasets.
"""

GROUNDING_SEARCH_SYSTEM = """\
You are the Grounding Agent. Score each candidate dataset card for its suitability to \
ground the given subtask. Consider: modality match, annotation structure, domain \
relevance, and whether the raw content plausibly supports the required reasoning.
Score 0-5, where 5 = ideal source. Respond with STRICT JSON: \
{"scores": [{"dataset_id": "...", "score": 0.0, "reason": "..."}]}
"""

GROUNDING_TRANSFORM_SYSTEM = """\
You are the Grounding Agent. For a (subtask, dataset) pair, design a TRANSFORMATION PLAN \
that realizes the subtask's samples from the raw dataset using available tools.

Available tools (tool name -> description):
__TOOL_DESCRIPTIONS__

The plan must be concrete: each step names one tool and its parameters. For example, a
multiple-choice QA subtask over narrative data typically uses:
1. context_construction  -> assemble the raw fields into a coherent context
2. question_generation   -> produce a question + answer + distractors from the context
Steps are applied in order per sample.

Respond with STRICT JSON only, in this schema:
{"plan": {"steps": [{"tool": "...", "params": {...}}]},
 "rationale": "why this realizes the subtask"}
"""

GROUNDING_SCORE_SYSTEM = """\
You are the Grounding Agent's Score-and-Filter module. Evaluate a transformation plan \
for a (subtask, dataset) pair along three dimensions, each 1-5:
- alignment:     how well the plan realizes the subtask's evaluation intent;
- robustness:    whether the transformation is reliable, reproducible, and unlikely to \
                 produce degenerate samples (e.g. unanswerable questions);
- signal_preservation: whether the produced items still depend on the intended \
                 capability/modality signal rather than shortcuts.
A plan is ACCEPTED only if alignment >= 4, robustness >= 3 and signal_preservation >= 3.
Respond with STRICT JSON: {"scores": {"alignment": 0, "robustness": 0, \
"signal_preservation": 0}, "accepted": true/false, "issues": "..."}
"""

ALLOCATION_SYSTEM = """\
You are the Allocation Agent. Distribute the total sample quota across grounded \
(subtask, dataset) pairs to produce a feasible benchmark specification.
Constraints:
- sum of quotas == target total size;
- each subtask gets a meaningful share (>= ~15% of target);
- no pair exceeds the dataset's available sample capacity;
- prefer diversity: use multiple datasets for a subtask when possible.
Respond with STRICT JSON:
{"allocations": [{"subtask_id": "...", "dataset_id": "...", "quota": 0}]}
"""

ALLOCATION_DIAGNOSE_SYSTEM = """\
You are the Allocation Agent. The proposed allocation is INFEASIBLE. Diagnose the \
structural cause (capacity bottleneck / quota conflict / subtask starvation) and propose \
concrete adjustments within the space of already-grounded candidates.
Respond with STRICT JSON: {"diagnosis": "...", "adjustments": "..."}
"""

SAMPLE_PLAN_SYSTEM = """\
You are the sample-level planner inside the Benchmark Executor. You are given:
- the dataset-level transformation plan (fixed scaffold),
- one concrete raw sample,
- the current state of the sample after previous steps.
For the NEXT step, output the tool to invoke and its concrete parameters, instantiated \
for THIS sample (real values from the sample, no placeholders). If all steps are done or \
the sample cannot proceed, mark it done.
Respond with STRICT JSON:
{"action": "run"|"done", "tool": "...", "params": {...}, "next_input": "field mapping", \
"notes": "..."}
"""

VERIFY_SYSTEM = """\
You are the verification layer of the Benchmark Executor. Judge whether the produced \
benchmark item is valid:
1. schema: required input/output fields present, correct types;
2. answer_type: the answer matches the required format; options unique and non-trivial;
3. semantic: the item is answerable, faithful to the source context/media, and the \
answer is not leaked by the question itself.
Respond with STRICT JSON:
{"valid": true/false, "reasons": {"schema": "...", "answer_type": "...", "semantic": "..."}, \
"fix_hint": "..."}
"""

LLM_TOOL_CONTEXT_CONSTRUCTION = """\
You are a data transformation tool. Assemble the given raw fields into a coherent, \
self-contained evaluation context for the subtask. Preserve all facts verbatim where \
possible; never invent facts. Output STRICT JSON: {"context": "..."}
"""

LLM_TOOL_QUESTION_GENERATION = """\
You are a data transformation tool. From the given context, produce one evaluation item \
for the subtask: a question that requires the target reasoning, and the correct answer.
- question must be answerable from the context alone (or the media provided);
- answer must be factually faithful to the context;
- do not leak the answer in the question wording.
Output STRICT JSON: {"question": "...", "answer": "..."}
"""

LLM_TOOL_DISTRACTOR_GENERATION = """\
You are a data transformation tool. Given a question, its correct answer and the source \
context, generate plausible but INCORRECT distractor options for a multiple-choice item.
- distractors must be wrong but tempting; grounded in the same context domain;
- must not overlap with the correct answer; each must be meaningfully different.
Output STRICT JSON: {"distractors": ["...", "...", "..."]}
"""


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Agent user-message builders
# ---------------------------------------------------------------------------
def design_propose_user(query: str, target_size: int, dataset_summary: str) -> str:
    return (
        f"# Evaluation goal\n{query}\n\n"
        f"# Target size\n{target_size} items total\n\n"
        f"# Available datasets (cards only)\n{dataset_summary}\n\n"
        "Propose the subtask set now."
    )


def design_revise_user(query: str, subtasks: list[dict], feedback: str) -> str:
    return (
        f"# Evaluation goal\n{query}\n\n"
        f"# Current subtasks\n{_dumps(subtasks)}\n\n"
        f"# Grounding feedback\n{feedback}\n\n"
        "Output the revised subtask set (STRICT JSON as before)."
    )


def grounding_preference_user(subtask: dict) -> str:
    return f"# Subtask\n{_dumps(subtask)}\n\nDescribe the dataset preference in one paragraph."


def grounding_search_user(subtask: dict, preference: str, cards: list[dict]) -> str:
    cards_txt = "\n".join(
        f"- [{c['dataset_id']}] {c['name']} | modalities={c['modalities']} "
        f"| tasks={c['tasks']} | domain={c['domain']}\n  {c['card_text'][:400]}"
        for c in cards
    )
    return (
        f"# Subtask\n{_dumps(subtask)}\n\n"
        f"# Dataset preference\n{preference}\n\n"
        f"# Candidate dataset cards\n{cards_txt}\n\n"
        "Score each dataset 0-5."
    )


def grounding_transform_user(subtask: dict, card: dict, tool_descriptions: str) -> str:
    return (
        f"# Subtask\n{_dumps(subtask)}\n\n"
        f"# Dataset card\n{_dumps(card)}\n\n"
        "# Tool descriptions\n{tool_descriptions}\n\n"
        "Design the transformation plan."
    )


def grounding_score_user(subtask: dict, card: dict, plan: dict) -> str:
    return (
        f"# Subtask\n{_dumps(subtask)}\n\n"
        f"# Dataset card\n{_dumps(card)}\n\n"
        f"# Transformation plan\n{_dumps(plan)}\n\n"
        "Score and decide accept/reject."
    )


def allocation_user(spec: dict, target_size: int, capacities: dict) -> str:
    return (
        f"# Grounded (subtask, dataset, plan) pairs\n{_dumps(spec)}\n\n"
        f"# Target total size\n{target_size}\n\n"
        f"# Per-dataset capacities (available samples)\n{_dumps(capacities)}\n\n"
        "Produce the allocation."
    )


def allocation_diagnose_user(allocation: list[dict], target_size: int, capacities: dict) -> str:
    return (
        f"# Proposed allocation\n{_dumps(allocation)}\n\n"
        f"# Target size\n{target_size}\n\n"
        f"# Capacities\n{_dumps(capacities)}\n\n"
        "Diagnose the infeasibility and propose adjustments."
    )


def sample_plan_user(subtask: dict, dataset_plan: dict, sample_fields: dict, state_fields: dict, step_index: int) -> str:
    return (
        f"# Subtask\n{_dumps(subtask)}\n\n"
        f"# Dataset-level transformation plan\n{_dumps(dataset_plan)}\n\n"
        f"# Raw sample fields\n{_dumps(sample_fields)}\n\n"
        f"# Current transformed state (step {step_index} executed so far)\n{_dumps(state_fields)}\n\n"
        "Decide the next action for THIS sample."
    )


def verify_user(subtask: dict, item: dict) -> str:
    return (
        f"# Subtask specification\n{_dumps(subtask)}\n\n"
        f"# Produced benchmark item\n{_dumps(item)}\n\n"
        "Verify the item."
    )


def llm_tool_user(subtask: dict, fields: dict) -> str:
    return (
        f"# Subtask\n{_dumps(subtask)}\n\n"
        f"# Available fields\n{_dumps(fields)}\n\n"
        "Produce the output now."
    )


def few_shot_block(examples: list) -> str:
    """Render knowledge-base examples as few-shot demonstrations.

    `examples` are KnowledgeBase.Example objects. The block instructs the model
    to follow the examples' structure and quality bar without copying content.
    """
    if not examples:
        return ""
    parts = [
        "## High-quality reference examples (follow their structure and quality "
        "bar; do NOT copy their content — produce an original item for THIS input)"
    ]
    for i, ex in enumerate(examples, 1):
        parts.append(f"### Example {i}")
        parts.append(f"Input fields:\n{_dumps(ex.input_fields)}")
        parts.append(f"Expected output:\n{_dumps(ex.output)}")
    return "\n\n".join(parts)
