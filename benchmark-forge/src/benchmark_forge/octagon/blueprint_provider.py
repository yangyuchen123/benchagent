from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain import SourceMode
from ..providers import SourceSample
from .knowledge import OctagonKnowledgeBase


@dataclass
class RAGEnvironmentBlueprintProvider:
    """Dynamic source for designing a new executable environment from a goal.

    It supplies retrieved precedents and an empty blueprint slot to Executor;
    it does not contain a prewritten benchmark. The model must synthesize the
    task/environment/artifact/scoring contract from the current user goal.
    """

    goal: str
    knowledge_base: OctagonKnowledgeBase
    capacity_hint: int = 1
    provider_id: str = "rag-environment-blueprint"
    source_mode: SourceMode = SourceMode.GENERATED_ENVIRONMENT

    def capacity(self) -> int:
        return self.capacity_hint

    def sample(self, limit: int, *, offset: int = 0) -> list[SourceSample]:
        end = min(offset + limit, self.capacity_hint)
        results: list[SourceSample] = []
        for index in range(offset, end):
            context = self.knowledge_base.context(
                self.goal,
                role="environment_blueprint",
                source_kinds=["environment_profile", "task_spec", "documentation"],
                limit=8,
                max_chars=12_000,
            )
            results.append(SourceSample(
                sample_id=f"blueprint-{index}",
                fields={
                    "user_goal": self.goal,
                    "retrieved_precedents": context["results"],
                    "synthesis_request": {
                        "kind": "new_executable_environment",
                        "protocol": "octagon.env.v1",
                        "must_be_open_task": True,
                        "must_define": [
                            "instruction", "agent_capabilities", "environment",
                            "artifacts", "scoring_dimensions", "observation_requirements",
                        ],
                        "do_not_copy_private_or_expected_data": True,
                        "do_not_convert_behavior_to_multiple_choice": True,
                    },
                },
            ))
        return results

    def inspect(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "kind": "generated_environment_blueprint",
            "source_mode": self.source_mode.value,
            "capacity": self.capacity(),
            "goal": self.goal,
            "protocol": "octagon.env.v1",
            "materialization": "contract first; eval-system adapter implements/runs it later",
            "honesty_boundary": "generated environment must use maturity=generated_contract or pending until materialized",
        }
