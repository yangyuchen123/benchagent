"""Sample-level realization (paper Sec. 3.2).

For each (dataset, subtask, quota) allocation, raw samples flow through an
interleaved orchestration-execution loop:

  Orchestration: an LLM specializes the dataset-level transformation plan to the
                 CURRENT sample state and picks the next action (tool + params).
  Execution:     the action is applied (LLM tools directly, pure tools with the
                 instantiated parameters); outputs feed back into the state.

The dataset-level plan acts as a scaffold: planning is adaptive but constrained by
t_i,j to prevent uncontrolled divergence across samples.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .. import prompts
from ..llm import LLMClient
from ..schemas import BenchmarkSample, DatasetInstance, SampleState, Subtask, TransformPlan
from .tools.registry import Tool

log = logging.getLogger(__name__)

MAX_STEPS_PER_SAMPLE = 8


class SamplePlanner:
    """Orchestrates one sample through its dataset-level transformation plan."""

    def __init__(self, llm: LLMClient, subtask: Subtask, plan: TransformPlan,
                 registry: dict[str, Tool], model: str | None = None):
        self.llm = llm
        self.subtask = subtask
        self.plan = plan
        self.registry = registry
        self.model = model

    def _next_action(self, raw: DatasetInstance, state: SampleState, step_index: int) -> dict[str, Any]:
        return self.llm.chat_json(
            prompts.SAMPLE_PLAN_SYSTEM,
            prompts.sample_plan_user(
                self.subtask.model_dump(),
                self.plan.model_dump(),
                raw.fields,
                state.fields,
                step_index,
            ),
            model=self.model,
        )

    def run(self, raw: DatasetInstance, index: int) -> SampleState:
        state = SampleState(index=index, subtask_id=self.subtask.id, dataset_id=raw.dataset_id)
        # seed the sample state with the raw source fields and the subtask's answer
        # type so downstream LLM tools can actually operate on the source material
        state.fields = dict(raw.fields)
        state.fields.setdefault("answer_type", self.subtask.answer_type)
        for step_i in range(len(self.plan.steps)):
            if step_i >= MAX_STEPS_PER_SAMPLE:
                break
            action = self._next_action(raw, state, step_i)
            kind = action.get("action", "run")
            if kind == "done":
                state.log.append(f"planner: done after {step_i} steps")
                break
            tool_name = action.get("tool")
            tool = self.registry.get(tool_name)
            if tool is None:
                state.log.append(f"planner: unknown tool {tool_name!r}; skipping")
                state.status = "failed"
                break
            params = action.get("params", {})
            try:
                new_fields = tool.fn(state.fields, params, self.llm, self.subtask)
                state.fields.update(new_fields)
                state.log.append(f"step{step_i}: {tool_name} -> {sorted(new_fields)}")
            except Exception as e:  # noqa: BLE001 - a failing step marks the sample failed
                state.log.append(f"step{step_i}: {tool_name} FAILED: {e}")
                state.status = "failed"
                break
        else:
            state.status = "done"
        if state.status == "pending":
            state.status = "done"
        return state


_OPTION_PATTERN = re.compile(r"([A-D])[.\\)]\s*(.+?)(?=\n\s*[A-D][.\\)]|\Z)", re.DOTALL)


def _embedded_options(question: str) -> list[str] | None:
    """Extract options embedded in the question text (e.g. 'A. xxx\nB. yyy').

    Returns None if the question carries no inline options, in which case the
    caller falls back to the assembled options array.
    """
    matches = _OPTION_PATTERN.findall(question)
    if len(matches) < 2:
        return None
    options = [m[1].strip() for m in matches]
    # keep only options whose letter sequence is contiguous from A
    expected = [chr(ord("A") + i) for i in range(len(matches))]
    if [m[0] for m in matches] != expected:
        return None
    return options


def state_to_sample(state: SampleState) -> BenchmarkSample | None:
    """Coerce a finished sample state into a benchmark item (best-effort)."""
    f = state.fields
    answer = f.get("answer", "")
    answer_type = f.get("answer_type", "multiple_choice")
    options = f.get("options")
    if answer_type == "multiple_choice":
        # Prefer options embedded in the question text (letters match the question
        # exactly); otherwise assemble answer + distractors.
        embedded = _embedded_options(f.get("question", ""))
        if embedded:
            options = embedded
        elif options is None:
            distractors = f.get("distractors") or []
            if answer and distractors:
                options = [answer] + [d for d in distractors if d != answer]
    else:
        # open-ended / true-false: conform to an empty options array
        options = []
    # if the answer is an option index, normalize to the option text
    if options and answer.isdigit() and 1 <= int(answer) <= len(options):
        answer = options[int(answer) - 1]
    try:
        return BenchmarkSample(
            subtask_id=state.subtask_id,
            dataset_id=state.dataset_id,
            sample_index=state.index,
            question=f.get("question", ""),
            context=f.get("context"),
            media=list(f.get("media") or []),
            options=options,
            answer=answer,
            answer_type=answer_type,
            meta={"log": state.log, "fields": {k: v for k, v in f.items() if k not in ("context", "question", "options", "distractors", "answer")}},
        )
    except Exception as e:  # noqa: BLE001
        log.warning("state_to_sample failed for sample %s: %s", state.index, e)
        return None
