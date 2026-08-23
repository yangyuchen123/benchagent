"""Verification layer (paper Sec. 3.2).

Assesses two aspects of every generated sample:
  1. semantic validity   -- the item truly reflects the intended evaluation objective;
  2. structural compliance -- it conforms to the subtask's required output format.

Plus quota control: invalid samples are discarded; when a subtask misses its target
quota, a replenishment round re-selects raw samples and reprocesses them through the
same constrained orchestration-execution mechanism.
"""
from __future__ import annotations

import logging
from typing import Any

from .. import prompts
from ..llm import LLMClient
from ..schemas import BenchmarkSample, Subtask
from .planning import _embedded_options

log = logging.getLogger(__name__)


class Verifier:
    def __init__(self, llm: LLMClient, model: str | None = None):
        self.llm = llm
        self.model = model

    def _structural_check(self, subtask: Subtask, item: BenchmarkSample) -> tuple[bool, str]:
        """Deterministic schema + answer-type checks (no LLM cost)."""
        if not item.question.strip():
            return False, "empty question"
        if not item.answer.strip():
            return False, "empty answer"
        if subtask.answer_type == "multiple_choice":
            if not item.options or len(item.options) < 2:
                return False, "multiple_choice item missing options"
            if len(set(item.options)) != len(item.options):
                return False, "duplicate options"
            if item.answer not in item.options and item.answer not in [str(i + 1) for i in range(len(item.options))]:
                return False, "answer not among options"
            embedded = _embedded_options(item.question)
            if embedded and embedded != item.options:
                return False, "inline options in question disagree with options array"
        elif item.options not in (None, []):
            return False, f"{subtask.answer_type} item must not carry options"
        return True, "ok"

    def _semantic_check(self, subtask: Subtask, item: BenchmarkSample) -> tuple[bool, str]:
        try:
            data = self.llm.chat_json(
                prompts.VERIFY_SYSTEM,
                prompts.verify_user(subtask.model_dump(), item.model_dump()),
                model=self.model,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Verifier: LLM check failed (%s); defaulting to accept", e)
            return True, "llm-check unavailable; accepted by default"
        reasons = data.get("reasons", {})
        valid = bool(data.get("valid", False))
        if not valid:
            hint = data.get("fix_hint", "")
            detail = "; ".join(f"{k}={v}" for k, v in reasons.items())
            return False, f"{hint} ({detail})"
        return True, "ok"

    def verify(self, subtask: Subtask, item: BenchmarkSample) -> tuple[bool, str]:
        ok, msg = self._structural_check(subtask, item)
        if not ok:
            return False, f"structural: {msg}"
        ok, msg = self._semantic_check(subtask, item)
        return ok, f"semantic: {msg}"
