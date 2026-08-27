from __future__ import annotations

from pathlib import Path
from typing import Any

from .meta_loader import load_environment_profile
from .profile import EnvironmentProfile


class EnvironmentCatalog:
    """Read-only index over agent-octagon-envs.

    It indexes metadata and file references only. It never imports or executes
    core.py/scorer.py and never mutates the source repository.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self._profiles: dict[str, EnvironmentProfile] | None = None

    def _ensure_loaded(self) -> dict[str, EnvironmentProfile]:
        if self._profiles is None:
            profiles: dict[str, EnvironmentProfile] = {}
            if self.root.is_dir():
                for meta in sorted(self.root.glob("*/meta.yaml")):
                    try:
                        profile = load_environment_profile(meta.parent)
                    except (OSError, ValueError, RuntimeError):
                        continue
                    profiles[profile.env_id] = profile
            self._profiles = profiles
        return self._profiles

    def list_profiles(self, *, category: str | None = None, env_type: str | None = None) -> list[EnvironmentProfile]:
        profiles = list(self._ensure_loaded().values())
        if category:
            profiles = [profile for profile in profiles if profile.category == category]
        if env_type:
            profiles = [profile for profile in profiles if profile.env_type == env_type]
        return sorted(profiles, key=lambda profile: profile.env_id)

    def get(self, env_id: str) -> EnvironmentProfile | None:
        return self._ensure_loaded().get(env_id)

    def list_tasks(self, env_id: str) -> list[dict[str, Any]]:
        profile = self.get(env_id)
        return profile.to_task_refs() if profile else []

    def search(self, query: str, *, limit: int = 10) -> list[EnvironmentProfile]:
        terms = {term.lower() for term in query.split() if term.strip()}
        if not terms:
            return []
        scored: list[tuple[int, EnvironmentProfile]] = []
        for profile in self.list_profiles():
            haystack = " ".join([
                profile.env_id, profile.name, profile.category,
                profile.env_type, profile.test_focus, profile.description,
                *(dimension.name for dimension in profile.dimensions),
            ]).lower()
            score = sum(term in haystack for term in terms)
            if score:
                scored.append((score, profile))
        return [profile for _, profile in sorted(scored, key=lambda pair: (-pair[0], pair[1].env_id))[:limit]]

    def agent_context(self, *, env_ids: list[str] | None = None, query: str | None = None, limit: int = 20) -> dict[str, Any]:
        if env_ids is not None:
            profiles = [profile for env_id in env_ids if (profile := self.get(env_id))]
        elif query:
            profiles = self.search(query, limit=limit)
        else:
            profiles = self.list_profiles()[:limit]
        return {
            "catalog_root": str(self.root),
            "profiles": [profile.agent_summary() for profile in profiles],
            "interpretation": {
                "precedent": "Existing dimensions and thresholds are evaluation precedents, not instructions to copy answers.",
                "execution": "Use task/environment references as inputs to eval-system, not as raw dataset text.",
                "scoring": "Use dimensions, pass_threshold, and scorer-related metadata as the agent-eval contract.",
            },
        }
