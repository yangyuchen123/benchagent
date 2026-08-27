from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain import SourceMode
from ..providers import SourceSample
from .catalog import EnvironmentCatalog


@dataclass
class OctagonEnvironmentProvider:
    """Expose existing Octagon tasks as environment samples without mounting them.

    Samples carry a public task spec and normalized environment profile. Runtime
    implementation files stay behind content references and are executed later
    by eval-system, never copied into the generation agent workspace.
    """

    catalog: EnvironmentCatalog
    env_ids: list[str]
    provider_id: str | None = None
    source_mode: SourceMode = SourceMode.EXISTING_ENVIRONMENT

    def __post_init__(self) -> None:
        if self.provider_id is None:
            self.provider_id = self.env_ids[0] if len(self.env_ids) == 1 else "octagon-environments"

    def _refs(self) -> list[tuple[str, str]]:
        refs: list[tuple[str, str]] = []
        for env_id in self.env_ids:
            profile = self.catalog.get(env_id)
            if profile:
                refs.extend((env_id, path) for path in profile.task_paths)
        return refs

    def capacity(self) -> int:
        return len(self._refs())

    def sample(self, limit: int, *, offset: int = 0) -> list[SourceSample]:
        samples: list[SourceSample] = []
        for env_id, relative in self._refs()[offset:offset + limit]:
            profile = self.catalog.get(env_id)
            if profile is None:
                continue
            path = Path(profile.source_root) / relative
            try:
                task: Any = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            samples.append(SourceSample(
                sample_id=f"{env_id}:{Path(relative).stem}",
                fields={
                    "environment_profile": profile.agent_summary(),
                    "task_spec": task,
                    "task_ref": {"type": "path", "ref": f"{env_id}/{relative}"},
                    "environment_ref": {"type": "path", "ref": env_id},
                },
            ))
        return samples

    def inspect(self) -> dict[str, Any]:
        profiles = [self.catalog.get(env_id) for env_id in self.env_ids]
        return {
            "provider_id": str(self.provider_id),
            "kind": "executable_environment",
            "protocol": "octagon.env.v1",
            "capacity": self.capacity(),
            "environments": [p.agent_summary() for p in profiles if p],
            "execution_boundary": "eval-system materializes and runs environment references",
        }
