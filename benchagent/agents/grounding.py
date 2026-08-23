"""Grounding Agent (paper Sec. 3.1).

Validates that every subtask admits at least one feasible realization on real data
through a three-step process:

  i)   Dataset Search   -- Preference tool characterizes the desired data, then
                           candidate datasets are retrieved and filtered.
  ii)  Transformability -- the Transformability tool builds candidate transformation
                           plans; the Score-and-Filter module keeps only plans that
                           pass alignment / robustness / signal-preservation checks.
  iii) Accept/Reject    -- the subtask set is accepted iff every subtask has at least
                           one valid grounding; otherwise feedback returns to Design.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .. import prompts
from ..dataset_pool import DatasetPool
from ..llm import LLMClient
from ..schemas import DatasetCard, Grounding, Subtask, TransformPlan, TransformStep
from ..executor.tools.registry import Tool, build_registry, tool_descriptions

log = logging.getLogger(__name__)

SCORE_THRESHOLDS = {"alignment": 4.0, "robustness": 3.0, "signal_preservation": 3.0}
TOP_K_DATASETS = 5
DEFAULT_WORKERS = 4


class GroundingAgent:
    def __init__(self, llm: LLMClient, pool: DatasetPool, model: str | None = None,
                 registry: dict[str, Tool] | None = None, workers: int = DEFAULT_WORKERS):
        self.llm = llm
        self.pool = pool
        self.model = model
        self.registry = registry or build_registry()
        self.workers = workers

    # -- step i: dataset search ------------------------------------------------
    def _preference(self, subtask: Subtask) -> str:
        data = self.llm.chat_json(
            prompts.GROUNDING_PREFERENCE_SYSTEM,
            prompts.grounding_preference_user(subtask.model_dump()),
            model=self.model,
        )
        if isinstance(data, dict):
            return data.get("preference") or data.get("description") or str(data)
        return str(data)

    def _search(self, subtask: Subtask, preference: str) -> list[DatasetCard]:
        cards = list(self.pool.cards.values())
        data = self.llm.chat_json(
            prompts.GROUNDING_SEARCH_SYSTEM,
            prompts.grounding_search_user(
                subtask.model_dump(), preference,
                [c.model_dump() for c in cards],
            ),
            model=self.model,
        )
        scored: list[tuple[float, DatasetCard]] = []
        for entry in data.get("scores", []):
            cid = entry.get("dataset_id")
            if cid in self.pool.cards:
                scored.append((float(entry.get("score", 0.0)), self.pool.cards[cid]))
        scored.sort(key=lambda x: x[0], reverse=True)
        keep = [c for s, c in scored if s >= 3.0][:TOP_K_DATASETS]
        log.info("Grounding: subtask %s -> %d candidate datasets (of %d scored)",
                 subtask.id, len(keep), len(scored))
        return keep

    # -- step ii: transformability validation ----------------------------------
    def _propose_plan(self, subtask: Subtask, card: DatasetCard) -> TransformPlan:
        system = prompts.GROUNDING_TRANSFORM_SYSTEM.replace(
            "__TOOL_DESCRIPTIONS__", tool_descriptions(self.registry)
        )
        data = self.llm.chat_json(
            system,
            prompts.grounding_transform_user(
                subtask.model_dump(), card.model_dump(),
                tool_descriptions(self.registry),
            ),
            model=self.model,
        )
        plan_data = data.get("plan", data)
        steps = [TransformStep(**s) for s in plan_data.get("steps", [])]
        return TransformPlan(steps=steps, rationale=data.get("rationale", ""))

    def _score_plan(self, subtask: Subtask, card: DatasetCard, plan: TransformPlan) -> tuple[bool, dict[str, float], str]:
        data = self.llm.chat_json(
            prompts.GROUNDING_SCORE_SYSTEM,
            prompts.grounding_score_user(subtask.model_dump(), card.model_dump(), plan.model_dump()),
            model=self.model,
        )
        scores = {k: float(v) for k, v in (data.get("scores") or {}).items()}
        issues = str(data.get("issues", "")).strip()
        # enforce thresholds deterministically even if the LLM is lenient
        accepted = all(scores.get(k, 0.0) >= t for k, t in SCORE_THRESHOLDS.items())
        return accepted, scores, issues

    def ground_subtask(self, subtask: Subtask) -> list[Grounding]:
        """Return all valid groundings for one subtask (empty if none)."""
        preference = self._preference(subtask)
        candidates = self._search(subtask, preference)
        groundings: list[Grounding] = []
        for card in candidates:
            try:
                plan = self._propose_plan(subtask, card)
            except Exception as e:  # noqa: BLE001 - a bad plan for one dataset is not fatal
                log.debug("Grounding: plan failed for %s/%s: %s", subtask.id, card.dataset_id, e)
                continue
            accepted, scores, issues = self._score_plan(subtask, card, plan)
            if accepted:
                groundings.append(Grounding(
                    subtask_id=subtask.id,
                    dataset_id=card.dataset_id,
                    plan=plan,
                    scores=scores,
                ))
                log.info("Grounding: valid grounding %s <- %s (scores=%s)",
                         subtask.id, card.dataset_id, scores)
            else:
                log.info("Grounding: rejected plan for %s <- %s (scores=%s) %s",
                         subtask.id, card.dataset_id, scores, issues[:120])
        return groundings

    def validate(self, subtasks: list[Subtask], memo: dict[tuple[str, str], list[Grounding]] | None = None,
                 ) -> tuple[bool, str, dict[str, list[Grounding]]]:
        """Validate all subtasks. Returns (all_grounded, feedback, groundings).

        `memo` caches groundings by (subtask_id, description) so that subtasks kept
        unchanged across design-loop rounds are not re-grounded (huge API-cost win).
        Subtasks are grounded in parallel (they are independent).
        """
        memo = memo if memo is not None else {}
        # resolve which subtasks need fresh grounding
        todo: list[Subtask] = []
        groundings: dict[str, list[Grounding]] = {}
        for s in subtasks:
            key = (s.id, s.description)
            if key in memo:
                groundings[s.id] = memo[key]
                log.debug("Grounding: reuse cached grounding for %s", s.id)
            else:
                todo.append(s)

        with ThreadPoolExecutor(max_workers=min(self.workers, max(1, len(todo)))) as ex:
            futures = {ex.submit(self.ground_subtask, s): s for s in todo}
            for fut in as_completed(futures):
                s = futures[fut]
                try:
                    gs = fut.result()
                except Exception as e:  # noqa: BLE001
                    log.error("Grounding: subtask %s failed with exception: %s", s.id, e)
                    gs = []
                groundings[s.id] = gs
                memo[(s.id, s.description)] = gs

        failures: list[str] = []
        for s in subtasks:
            if not groundings.get(s.id):
                failures.append(
                    f"subtask '{s.id}' ({s.name}): NO dataset among "
                    f"[{', '.join(sorted(self.pool.cards))}] passed the transformability "
                    f"check (alignment>=4, robustness>=3, signal_preservation>=3). "
                    f"Reasons for rejection are in the grounding log. Suggest a simpler "
                    f"subtask that can be realized on the available datasets with the "
                    f"registered tools, or reuse the successfully grounded subtasks."
                )
        if failures:
            feedback = (
                "Grounding failed for the following subtasks:\n- " + "\n- ".join(failures)
            )
            return False, feedback, groundings
        return True, "", groundings
