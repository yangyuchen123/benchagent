from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EnvironmentDimension(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    weight: float = 0
    description: str = ""


class EnvironmentProfile(BaseModel):
    """Read-only normalized view of an agent-octagon environment.

    This is intentionally a catalog profile, not an execution adapter.  The
    source environment remains authoritative for task execution and scoring.
    """

    model_config = ConfigDict(extra="allow")

    env_id: str
    name: str
    schema_version: str = "1.0"
    env_type: str = "unknown"
    category: str = "uncategorized"
    test_focus: str = ""
    description: str = ""
    pass_threshold: float | None = None
    prerequisites: dict[str, Any] = Field(default_factory=dict)
    entrypoints: dict[str, Any] = Field(default_factory=dict)
    dimensions: list[EnvironmentDimension] = Field(default_factory=list)
    task_paths: list[str] = Field(default_factory=list)
    material_paths: list[str] = Field(default_factory=list)
    source_root: str
    meta_path: str

    @property
    def requires(self) -> list[str]:
        requires = self.prerequisites.get("requires", [])
        return [str(value) for value in requires] if isinstance(requires, list) else []

    @property
    def prerequisite_level(self) -> str:
        return str(self.prerequisites.get("level", "none"))

    @property
    def executable(self) -> bool:
        """Whether the profile has at least one discoverable task.

        This does not assert that dependencies are installed or that a trial
        can run; those checks belong to eval-system.
        """
        return bool(self.task_paths)

    def agent_summary(self) -> dict[str, Any]:
        """Compact context suitable for a role agent prompt/tool result."""
        return {
            "env_id": self.env_id,
            "name": self.name,
            "type": self.env_type,
            "category": self.category,
            "test_focus": self.test_focus,
            "description": self.description,
            "pass_threshold": self.pass_threshold,
            "prerequisite_level": self.prerequisite_level,
            "requires": self.requires,
            "entrypoints": sorted(self.entrypoints),
            "task_count": len(self.task_paths),
            "material_count": len(self.material_paths),
            "dimensions": [dimension.model_dump(mode="json") for dimension in self.dimensions],
            "execution_boundary": "eval-system runs trials; agent-eval scores TrialResult/artifacts",
        }

    def to_task_refs(self) -> list[dict[str, Any]]:
        return [
            {"task_id": f"{self.env_id}:{Path(path).stem}", "path": path, "env_id": self.env_id}
            for path in self.task_paths
        ]
