from __future__ import annotations

from pathlib import Path
from typing import Any

from .profile import EnvironmentDimension, EnvironmentProfile


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised in minimal installs
        raise RuntimeError("Octagon catalog requires PyYAML; install pyyaml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _files(root: Path, directory: str) -> list[str]:
    path = root / directory
    if not path.exists():
        return []
    return sorted(str(item.relative_to(root)) for item in path.rglob("*") if item.is_file())


def load_environment_profile(env_dir: str | Path) -> EnvironmentProfile:
    root = Path(env_dir).expanduser().resolve()
    meta_path = root / "meta.yaml"
    if not meta_path.is_file():
        raise FileNotFoundError(f"missing meta.yaml: {meta_path}")
    raw = _load_yaml(meta_path)
    dimensions = [EnvironmentDimension.model_validate(item) for item in (raw.get("dimensions") or []) if isinstance(item, dict) and item.get("name")]
    prerequisites = raw.get("prerequisites") if isinstance(raw.get("prerequisites"), dict) else {}
    entrypoints = raw.get("entrypoints") if isinstance(raw.get("entrypoints"), dict) else {}
    return EnvironmentProfile(
        env_id=str(raw.get("name") or root.name),
        name=str(raw.get("name") or root.name),
        schema_version=str(raw.get("schema_version") or "1.0"),
        env_type=str(raw.get("type") or "unknown"),
        category=str(raw.get("category") or "uncategorized"),
        test_focus=str(raw.get("test_focus") or ""),
        description=str(raw.get("description") or ""),
        pass_threshold=float(raw["pass_threshold"]) if raw.get("pass_threshold") is not None else None,
        prerequisites=prerequisites,
        entrypoints=entrypoints,
        dimensions=dimensions,
        task_paths=_files(root, "tasks"),
        material_paths=_files(root, "materials"),
        source_root=str(root),
        meta_path=str(meta_path),
    )
