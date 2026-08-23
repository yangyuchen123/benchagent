"""End-to-end pipeline: user query -> verified benchmark.

Planner (Design -> Grounding -> Allocation) and Executor (Sample Realization ->
Verification with quota replenishment) are orchestrated here with full intermediate
caching under `cache/{query_id}/`, enabling incremental reruns and manual edits
between stages (reproducibility, as emphasized in the paper).
"""
from __future__ import annotations

import json
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from .agents.allocation import AllocationAgent
from .agents.design import DesignAgent
from .agents.grounding import GroundingAgent
from .config import load_model_config
from .dataset_pool import DatasetPool
from .executor.planning import SamplePlanner, state_to_sample
from .executor.tools.registry import build_registry
from .executor.verification import Verifier
from .llm import LLMClient
from .schemas import BenchmarkSample, BenchmarkSpec, Subtask, UserQuery

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, *, model_config: dict[str, Any], dataset_pool: DatasetPool,
                 cache_path: str = "cache", llm: Optional[LLMClient] = None,
                 seed: int = 0, sample_cap: Optional[int] = None, workers: int = 4):
        self.model_config = model_config
        self.pool = dataset_pool
        self.cache_path = cache_path
        self.llm = llm or LLMClient(model_config)
        self.registry = build_registry()
        self.seed = seed
        self.sample_cap = sample_cap  # optional cap on raw samples used per dataset (testing)
        self.workers = workers
        self._grounding_memo: dict[tuple[str, str], list] = {}  # (subtask_id, description) -> groundings
        self.agents = {
            "design": model_config["agents"].get("design") or model_config["default_model"],
            "grounding": model_config["agents"].get("grounding") or model_config["default_model"],
            "allocation": model_config["agents"].get("allocation") or model_config["default_model"],
            "executor": model_config["agents"].get("executor") or model_config["default_model"],
            "verify": model_config["agents"].get("verify") or model_config["default_model"],
            "tools": model_config["tools"].get("default") or model_config["default_model"],
        }

    # ------------------------------------------------------------------ cache
    def _cache_file(self, query_id: str, name: str) -> str:
        d = os.path.join(self.cache_path, query_id)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    def _load_cache(self, query_id: str, name: str) -> Optional[dict]:
        p = self._cache_file(query_id, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _save_cache(self, query_id: str, name: str, obj: Any) -> None:
        with open(self._cache_file(query_id, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ planner
    def _dataset_summary(self) -> str:
        lines = []
        for c in self.pool.cards.values():
            lines.append(f"- [{c.dataset_id}] {c.name} | {c.modality_label} | tasks={c.tasks} | {c.domain}")
        return "\n".join(lines)

    def _grounding_validate(self, subtasks: list[Subtask], query: UserQuery,
                            groundings_store: dict):
        """Callback used by the Design Agent: run grounding, cache results."""
        agent = GroundingAgent(self.llm, self.pool, model=self.agents["grounding"],
                               registry=self.registry, workers=self.workers)
        ok, feedback, groundings = agent.validate(subtasks, memo=self._grounding_memo)
        groundings_store["by_subtask"] = {
            k: [g.model_dump() for g in v] for k, v in groundings.items()
        }
        groundings_store["ok"] = ok
        groundings_store["feedback"] = feedback
        self._save_cache(query.id, "groundings.json", groundings_store)
        return ok, feedback

    def plan(self, query: UserQuery) -> Optional[BenchmarkSpec]:
        """Run the Benchmark Planner with caching."""
        cache = self._load_cache(query.id, "spec.json")
        if cache:
            log.info("Planner: loading cached benchmark spec")
            return BenchmarkSpec.model_validate(cache)

        design = DesignAgent(self.llm, model=self.agents["design"])
        groundings_store: dict[str, Any] = {}
        subtasks, feedback = design.run(
            query, self._dataset_summary(),
            lambda st: self._grounding_validate(st, query, groundings_store),
        )
        if feedback:
            # Graceful degradation: if the design loop could not ground every subtask,
            # discard the ungroundable ones (per the paper's Discard operation) and
            # proceed with the grounded subset.
            by_subtask = groundings_store.get("by_subtask") or {}
            grounded_ids = [sid for sid, gs in by_subtask.items() if gs]
            kept = [s for s in subtasks if s.id in grounded_ids]
            if kept:
                log.warning("Planner: discarding ungroundable subtasks; keeping %d/%d grounded",
                            len(kept), len(subtasks))
                subtasks = kept
            else:
                log.error("Planner: could not ground any subtask after retries: %s", feedback)
                return None

        # rebuild grounding objects from cache
        from .schemas import Grounding, TransformPlan, TransformStep
        groundings: dict[str, list[Grounding]] = {}
        for sid, gs in (groundings_store.get("by_subtask") or {}).items():
            groundings[sid] = [
                Grounding(
                    subtask_id=g["subtask_id"], dataset_id=g["dataset_id"],
                    plan=TransformPlan(steps=[TransformStep(**s) for s in g["plan"]["steps"]],
                                       rationale=g["plan"].get("rationale", "")),
                    scores=g.get("scores", {}),
                )
                for g in gs
            ]

        allocation = AllocationAgent(self.llm, self.pool, model=self.agents["allocation"])
        spec = allocation.build_spec(query, subtasks, groundings)
        if spec is None:
            log.error("Planner: allocation failed")
            return None
        self._save_cache(query.id, "spec.json", spec.model_dump())
        log.info("Planner: spec saved (subtasks=%d, allocations=%d)",
                 len(spec.subtasks), len(spec.allocations))
        return spec

    # ------------------------------------------------------------ executor
    def execute(self, spec: BenchmarkSpec) -> list[BenchmarkSample]:
        """Run sample realization + verification with quota replenishment."""
        samples: list[BenchmarkSample] = []
        verifier = Verifier(self.llm, model=self.agents["verify"])
        subtask_by_id = {s.id: s for s in spec.subtasks}

        for alloc in spec.allocations:
            subtask = subtask_by_id[alloc.subtask_id]
            card = self.pool.cards[alloc.dataset_id]
            data_cfg = self.pool_data_config(card.dataset_id)
            instances = self.pool.load(alloc.dataset_id, data_cfg)
            if self.sample_cap:
                instances = instances[: self.sample_cap]
            rng = random.Random(self.seed + alloc.subtask_id.__hash__() % (2**32))
            rng.shuffle(instances)

            produced = self._realize(alloc.subtask_id, alloc.dataset_id, alloc.plan,
                                     subtask, instances)
            # verification + replenishment loop
            accepted: list[BenchmarkSample] = []
            cursor = 0
            while len(accepted) < alloc.quota and cursor < len(instances) * 3:
                if cursor >= len(produced):
                    break
                item = produced[cursor]
                cursor += 1
                ok, msg = verifier.verify(subtask, item)
                if ok:
                    accepted.append(item)
                    log.debug("verify: accepted sample %s", item.sample_index)
                else:
                    log.debug("verify: rejected sample %s (%s)", item.sample_index, msg)
            samples.extend(accepted)
            log.info("Executor: subtask %s on %s: accepted %d/%d (quota %d)",
                     alloc.subtask_id, alloc.dataset_id, len(accepted), len(produced), alloc.quota)

        return samples

    def _realize(self, subtask_id: str, dataset_id: str, plan, subtask: Subtask,
                 instances: list) -> list[BenchmarkSample]:
        """Orchestrate + execute all raw instances for one allocation, in parallel.

        Each sample's orchestration loop is independent, so samples run concurrently
        (paper: execution is parallelized across samples).
        """
        planner = SamplePlanner(self.llm, subtask, plan, self.registry,
                                model=self.agents["executor"])
        produced: list[BenchmarkSample] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, max(1, len(instances)))) as ex:
            futures = {ex.submit(planner.run, inst, inst.index): inst for inst in instances}
            for fut in as_completed(futures):
                state = fut.result()
                if state.status == "done":
                    item = state_to_sample(state)
                    if item:
                        produced.append(item)
        return produced

    # ------------------------------------------------------------ data config
    def pool_data_config(self, dataset_id: str) -> dict[str, Any]:
        """Retrieve the data config for a dataset from the pool's config file.

        Stored at pool construction time as `_data_cfgs`.
        """
        return self.pool._data_cfgs.get(dataset_id, {})

    # ------------------------------------------------------------ top level
    def run(self, query: UserQuery) -> Optional[list[BenchmarkSample]]:
        spec = self.plan(query)
        if spec is None:
            return None
        samples = self.execute(spec)
        out_path = self._cache_file(query.id, "evaluation.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "query": query.model_dump(),
                    "spec": spec.model_dump(),
                    "samples": [s.model_dump() for s in samples],
                },
                f, ensure_ascii=False, indent=2,
            )
        log.info("Pipeline: wrote %d samples to %s", len(samples), out_path)
        return samples


def run_pipeline(*, query_path: str, model_config_path: str, dataset_config_path: str,
                 data_root: Optional[str] = None, cache_path: str = "cache",
                 seed: int = 0, sample_cap: Optional[int] = None, workers: int = 4,
                 model: Optional[str] = None) -> Optional[list[BenchmarkSample]]:
    """Convenience entry point used by the CLI."""
    with open(query_path, "r", encoding="utf-8") as f:
        query = UserQuery(**json.load(f))
    model_config = load_model_config(model_config_path)
    if model:
        # CLI wins: override every role with the given model
        model_config = dict(model_config)
        model_config["default_model"] = model
        model_config["agents"] = {k: model for k in model_config["agents"]}
        model_config["tools"] = {k: model for k in model_config["tools"]}
    pool = DatasetPool.from_config(dataset_config_path, data_root=data_root)
    pipe = Pipeline(model_config=model_config, dataset_pool=pool,
                    cache_path=cache_path, seed=seed, sample_cap=sample_cap,
                    workers=workers)
    return pipe.run(query)
