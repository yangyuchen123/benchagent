from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .domain import SourceMode


@dataclass(frozen=True)
class SourceSample:
    sample_id: str
    fields: dict[str, Any]


class SourceProvider(Protocol):
    provider_id: str
    source_mode: SourceMode

    def capacity(self) -> int: ...
    def sample(self, limit: int, *, offset: int = 0) -> list[SourceSample]: ...
    def inspect(self) -> dict[str, Any]: ...


@dataclass
class DatasetProvider:
    provider_id: str
    rows: list[dict[str, Any]]
    source_mode: SourceMode = SourceMode.EXISTING_DATASET

    def capacity(self) -> int:
        return len(self.rows)

    def sample(self, limit: int, *, offset: int = 0) -> list[SourceSample]:
        rows = self.rows[offset:offset + limit]
        return [SourceSample(str(offset + i), row) for i, row in enumerate(rows)]

    def inspect(self) -> dict[str, Any]:
        fields = sorted({k for row in self.rows for k in row})
        return {"provider_id": self.provider_id, "capacity": self.capacity(), "fields": fields}


@dataclass
class ProceduralProvider:
    provider_id: str = "procedural_default"
    source_mode: SourceMode = SourceMode.SYNTHETIC
    capacity_hint: int = 100

    def capacity(self) -> int:
        return self.capacity_hint

    def sample(self, limit: int, *, offset: int = 0) -> list[SourceSample]:
        end = min(offset + limit, self.capacity_hint)
        return [SourceSample(str(i), {
            "subject": f"synthetic subject {i}",
            "facts": f"The subject has attribute {i % 3} and event number {i}.",
        }) for i in range(offset, end)]

    def inspect(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "capacity": self.capacity(), "kind": "procedural"}
