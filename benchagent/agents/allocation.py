"""Allocation Agent (paper Sec. 3.1).

Given grounded instantiations (s_i, d_i,j, t_i,j), decides whether the benchmark can
be instantiated under global quota/resource constraints through a closed loop:
  Allocate -> Diagnose -> Adjust -> re-Allocate
until a feasible allocation is found or no admissible adjustment remains.
"""
from __future__ import annotations

import logging
from typing import Any

from .. import prompts
from ..dataset_pool import DatasetPool
from ..llm import LLMClient
from ..schemas import AllocationItem, BenchmarkSpec, Grounding, Subtask, UserQuery

log = logging.getLogger(__name__)

MAX_LOOPS = 3
MIN_SUBTASK_SHARE = 0.15  # each subtask gets >= 15% of the target size


class AllocationAgent:
    def __init__(self, llm: LLMClient, pool: DatasetPool, model: str | None = None):
        self.llm = llm
        self.pool = pool
        self.model = model

    # -- capacity bookkeeping --------------------------------------------------
    def _capacity(self, subtasks: list[Subtask], groundings: dict[str, list[Grounding]]) -> dict[tuple[str, str], int]:
        """Maximum usable samples per (subtask, dataset) pair.

        Capacity is bounded by the dataset's sample count (read from the card) and,
        for MVP, a per-pair cap so one pair cannot monopolize a benchmark.
        """
        cap: dict[tuple[str, str], int] = {}
        for s in subtasks:
            for g in groundings.get(s.id, []):
                card = self.pool.cards[g.dataset_id]
                usable = card.size_samples or 100_000
                cap[(g.subtask_id, g.dataset_id)] = min(usable, 200)
        return cap

    # -- feasibility check -----------------------------------------------------
    def _is_feasible(self, allocations: list[AllocationItem], target: int,
                     capacity: dict[tuple[str, str], int]) -> tuple[bool, str]:
        total = sum(a.quota for a in allocations)
        if total != target:
            return False, f"total quota {total} != target size {target}"
        per_subtask: dict[str, int] = {}
        for a in allocations:
            per_subtask[a.subtask_id] = per_subtask.get(a.subtask_id, 0) + a.quota
            if a.quota > capacity.get((a.subtask_id, a.dataset_id), 0):
                return False, (
                    f"pair ({a.subtask_id}, {a.dataset_id}) quota {a.quota} exceeds "
                    f"capacity {capacity.get((a.subtask_id, a.dataset_id), 0)}"
                )
        n = len(per_subtask)
        for sid, q in per_subtask.items():
            if q < MIN_SUBTASK_SHARE * target:
                return False, f"subtask {sid} share {q} below minimum {MIN_SUBTASK_SHARE * target:.0f}"
        return True, ""

    # -- closed-loop allocation ------------------------------------------------
    def run(self, query: UserQuery, subtasks: list[Subtask],
            groundings: dict[str, list[Grounding]]) -> list[AllocationItem] | None:
        capacity = self._capacity(subtasks, groundings)
        # flatten grounded pairs
        pairs = [
            {
                "subtask_id": g.subtask_id,
                "dataset_id": g.dataset_id,
                "plan": g.plan.model_dump(),
                "scores": g.scores,
                "capacity": capacity[(g.subtask_id, g.dataset_id)],
            }
            for s in subtasks
            for g in groundings.get(s.id, [])
        ]
        if not pairs:
            log.error("Allocation: no grounded pairs to allocate")
            return None

        allocation: list[AllocationItem] | None = None
        for _ in range(MAX_LOOPS):
            data = self.llm.chat_json(
                prompts.ALLOCATION_SYSTEM,
                prompts.allocation_user(
                    {"pairs": pairs},
                    query.target_size,
                    {f"{p['subtask_id']}::{p['dataset_id']}": p["capacity"] for p in pairs},
                ),
                model=self.model,
            )
            proposed = [
                AllocationItem(
                    subtask_id=a["subtask_id"],
                    dataset_id=a["dataset_id"],
                    plan=next(p["plan"] for p in pairs if p["subtask_id"] == a["subtask_id"] and p["dataset_id"] == a["dataset_id"]),
                    quota=int(a["quota"]),
                )
                for a in data.get("allocations", [])
            ]
            ok, msg = self._is_feasible(proposed, query.target_size, capacity)
            if ok:
                allocation = proposed
                log.info("Allocation: feasible (total=%d)", sum(a.quota for a in proposed))
                break
            log.warning("Allocation: infeasible (%s); diagnosing...", msg)
            self.llm.chat_json(
                prompts.ALLOCATION_DIAGNOSE_SYSTEM,
                prompts.allocation_diagnose_user(
                    [a.model_dump() for a in proposed],
                    query.target_size,
                    {f"{p['subtask_id']}::{p['dataset_id']}": p["capacity"] for p in pairs},
                ),
                model=self.model,
            )
        return allocation

    def build_spec(self, query: UserQuery, subtasks: list[Subtask],
                   groundings: dict[str, list[Grounding]]) -> BenchmarkSpec | None:
        allocations = self.run(query, subtasks, groundings)
        if not allocations:
            return None
        return BenchmarkSpec(user_query=query, subtasks=subtasks, allocations=allocations)
