"""Design Agent (paper Sec. 3.1).

Converts the informal user requirement into a structured subtask set, iterating
through Propose -> Revise -> Discard until the set stabilizes, and reacting to
grounding feedback when a subtask cannot be realized on real data.
"""
from __future__ import annotations

import logging
from typing import Any

from .. import prompts
from ..llm import LLMClient
from ..schemas import Subtask, UserQuery

log = logging.getLogger(__name__)

MAX_ROUNDS = 4


class DesignAgent:
    def __init__(self, llm: LLMClient, model: str | None = None):
        self.llm = llm
        self.model = model

    def _parse_subtasks(self, data: Any) -> list[Subtask]:
        raw_list = data.get("subtasks") if isinstance(data, dict) else data
        if not isinstance(raw_list, list):
            raise ValueError(f"Unexpected subtask payload: {str(data)[:200]}")
        subtasks = []
        allowed_status = {"proposed", "grounded", "rejected"}
        for i, raw in enumerate(raw_list):
            raw = dict(raw)
            raw.setdefault("id", f"subtask_{i + 1}")
            if raw.get("status") not in allowed_status:
                raw["status"] = "proposed"  # normalize LLM-invented states
            subtasks.append(Subtask(**raw))
        return subtasks

    def propose(self, query: UserQuery, dataset_summary: str) -> list[Subtask]:
        """Propose an initial subtask set from the evaluation goal."""
        data = self.llm.chat_json(
            prompts.DESIGN_PROPOSE_SYSTEM,
            prompts.design_propose_user(query.description, query.target_size, dataset_summary),
            model=self.model,
        )
        subtasks = self._parse_subtasks(data)
        log.info("Design: proposed %d subtasks", len(subtasks))
        return subtasks

    def revise(self, query: UserQuery, subtasks: list[Subtask], feedback: str) -> list[Subtask]:
        """Revise the subtask set in response to grounding feedback."""
        data = self.llm.chat_json(
            prompts.DESIGN_REVISE_SYSTEM,
            prompts.design_revise_user(
                query.description,
                [s.model_dump() for s in subtasks],
                feedback,
            ),
            model=self.model,
        )
        revised = self._parse_subtasks(data)
        log.info("Design: revised to %d subtasks after feedback", len(revised))
        return revised

    def run(self, query: UserQuery, dataset_summary: str,
            grounding_validate) -> tuple[list[Subtask], str]:
        """Full design loop with grounding feedback.

        `grounding_validate(subtasks) -> (ok: bool, feedback: str)` is provided by
        the pipeline; when ok is False, the Design Agent revises and retries.
        """
        subtasks = self.propose(query, dataset_summary)
        for _ in range(MAX_ROUNDS):
            ok, feedback = grounding_validate(subtasks)
            if ok:
                return subtasks, ""
            log.warning("Design: grounding rejected subtask set: %s", feedback[:200])
            subtasks = self.revise(query, subtasks, feedback)
        ok, feedback = grounding_validate(subtasks)
        return subtasks, "" if ok else feedback
