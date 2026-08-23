"""End-to-end smoke test using a deterministic mock LLM (no API key needed).

Verifies the full pipeline: Design -> Grounding -> Allocation -> Sample
Realization -> Verification, plus caching/resume behavior.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from benchagent.config import load_model_config
from benchagent.dataset_pool import DatasetPool
from benchagent.pipeline import Pipeline
from benchagent.schemas import UserQuery


class MockLLM:
    """Responds to each agent prompt with a canned, structurally valid answer."""

    def chat(self, system, user, **kw):
        return "mock reply"

    def chat_json(self, system, user, **kw):
        if "Design Agent" in system:
            return {"subtasks": [
                {"id": "perspective_integration", "name": "Multi-perspective Integration",
                 "description": "Integrate multiple accounts of one event into a coherent understanding",
                 "modalities": ["text"], "answer_type": "multiple_choice",
                 "output_schema": {"question": "str", "options": "list", "answer": "str"}},
                {"id": "event_reconstruction", "name": "Event Reconstruction",
                 "description": "Reconstruct what actually happened from scattered accounts",
                 "modalities": ["text"], "answer_type": "multiple_choice",
                 "output_schema": {"question": "str", "options": "list", "answer": "str"}},
            ]}
        if "Revise" in system and "Grounding Agent rejected" in system:
            return {"subtasks": [
                {"id": "perspective_integration", "name": "Multi-perspective Integration",
                 "description": "Integrate multiple accounts of one event",
                 "modalities": ["text"], "answer_type": "multiple_choice",
                 "output_schema": {"question": "str", "options": "list", "answer": "str"}},
            ]}
        if "Grounding Agent. For a given subtask" in system:
            return {"preference": "text documents containing multiple accounts of shared events"}
        if "Score each candidate dataset card" in system:
            return {"scores": [
                {"dataset_id": "news_events", "score": 5.0, "reason": "perfect modality match"},
                {"dataset_id": "medical_snippets", "score": 1.5, "reason": "wrong domain"},
            ]}
        if "TRANSFORMATION PLAN" in system:
            return {"plan": {"steps": [
                {"tool": "context_construction", "params": {"input_fields": ["reports"]}},
                {"tool": "question_generation", "params": {"answer_type": "multiple_choice"}},
                {"tool": "distractor_generation", "params": {"num_distractors": 3}},
            ]}, "rationale": "turn reports into a grounded multi-perspective QA item"}
        if "Score-and-Filter module" in system:
            return {"scores": {"alignment": 5, "robustness": 4, "signal_preservation": 4},
                    "accepted": True, "issues": ""}
        if "Allocation Agent. Distribute" in system:
            return {"allocations": [
                {"subtask_id": "perspective_integration", "dataset_id": "news_events", "quota": 5},
                {"subtask_id": "event_reconstruction", "dataset_id": "news_events", "quota": 5},
            ]}
        if "Allocation Agent. The proposed allocation is INFEASIBLE" in system:
            return {"diagnosis": "quota exceeds capacity", "adjustments": "reduce quotas"}
        if "sample-level planner" in system:
            # decide the next tool from the current transformed state (deterministic)
            state_marker = user.split("Current transformed state", 1)[1].split("\n\n", 1)[0]
            if '"question"' in state_marker:
                return {"action": "run", "tool": "distractor_generation", "params": {}, "notes": ""}
            if '"context"' in state_marker:
                return {"action": "run", "tool": "question_generation", "params": {}, "notes": ""}
            return {"action": "run", "tool": "context_construction", "params": {}, "notes": ""}
        if "verification layer" in system:
            return {"valid": True, "reasons": {"schema": "ok", "answer_type": "ok", "semantic": "ok"}}
        if "Assemble the given raw fields" in system:
            return {"context": "Three reports describe the Riverside Bridge closure."}
        if "produce one evaluation item" in system:
            return {"question": "What did all reports agree on?", "answer": "The bridge will close Monday."}
        if "generate plausible but INCORRECT distractor" in system:
            return {"distractors": ["The bridge is open", "No closure planned", "The bridge collapsed"]}
        raise AssertionError(f"unhandled prompt: {system[:80]}...")

    def chat_enum(self, system, user, choices, **kw):
        return choices[0]


def main():
    query = UserQuery(id="smoke_test", description="multi-perspective reasoning", target_size=10)
    model_config = load_model_config("config/models.yaml")
    model_config["api_key"] = "mock"
    pool = DatasetPool.from_config("config/datasets.yaml", data_root="examples/data")
    pipe = Pipeline(model_config=model_config, dataset_pool=pool,
                    cache_path="/tmp/benchagent_cache", llm=MockLLM())
    pipe.llm = MockLLM()

    samples = pipe.run(query)
    assert samples is not None and len(samples) == 10, f"expected 10 samples, got {len(samples or [])}"
    for s in samples:
        assert s.question and s.answer, s
        assert len(s.options) >= 2, s
    print(f"PASS: {len(samples)} verified samples generated")

    # caching/resume: second run should reuse the spec cache
    pipe2 = Pipeline(model_config=model_config, dataset_pool=pool,
                     cache_path="/tmp/benchagent_cache", llm=MockLLM())
    pipe2.llm = MockLLM()
    samples2 = pipe2.run(query)
    assert len(samples2) == 10
    print("PASS: cache/resume works")

    # cache artifact layout
    assert os.path.exists("/tmp/benchagent_cache/smoke_test/spec.json")
    assert os.path.exists("/tmp/benchagent_cache/smoke_test/evaluation.json")
    print("PASS: cache artifacts written")


if __name__ == "__main__":
    main()
