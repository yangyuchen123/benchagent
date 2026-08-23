"""Dataset pool: registry of dataset cards plus loaders for raw samples.

Two loading strategies are supported out of the box:
  - `json` loader: a JSON/JSONL file whose records are samples (fields = record),
    optionally with relative media paths resolved against the file directory.
  - `directory` loader: a folder of media files (e.g. images) plus an optional
    JSON annotations file mapping filename -> fields.

Any Hugging Face dataset can be dropped in by adding a loader in
`register_loader` (see the optional `hf` extra).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .schemas import DatasetCard, DatasetInstance

Loader = Callable[[DatasetCard, dict[str, Any]], list[DatasetInstance]]


@dataclass
class DatasetPool:
    """Holds dataset cards and lazy loaders."""

    cards: dict[str, DatasetCard] = field(default_factory=dict)
    _loaders: dict[str, Loader] = field(default_factory=dict)
    _data_cfgs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add_card(self, card: DatasetCard) -> None:
        self.cards[card.dataset_id] = card

    def register(self, name: str, loader: Loader) -> None:
        self._loaders[name] = loader

    def load(self, dataset_id: str, data_cfg: dict[str, Any]) -> list[DatasetInstance]:
        card = self.cards[dataset_id]
        loader_name = data_cfg.get("loader", "json")
        loader = self._loaders.get(loader_name)
        if loader is None:
            raise KeyError(f"Unknown loader {loader_name!r} for dataset {dataset_id}")
        return loader(card, data_cfg)

    @classmethod
    def from_config(cls, config_path: str, data_root: str | None = None) -> "DatasetPool":
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        pool = cls()
        pool.register("json", load_json)
        pool.register("directory", load_directory)
        for entry in cfg.get("datasets", []):
            card = DatasetCard(**entry["card"])
            pool.add_card(card)
            data_cfg = dict(entry.get("data", {}))
            if data_root:
                if "path" in data_cfg and not os.path.isabs(data_cfg["path"]):
                    # relative data file -> resolve against data_root
                    data_cfg["path"] = os.path.join(data_root, data_cfg["path"])
                elif "root" not in data_cfg:
                    # media-folder loader -> convention: data_root/<dataset_id>/
                    data_cfg["root"] = os.path.join(data_root, card.dataset_id)
            pool._data_cfgs[card.dataset_id] = data_cfg
        return pool




def _resolve_media(path: str, root: str) -> str:
    return path if os.path.isabs(path) else os.path.join(root, path)


def load_json(card: DatasetCard, cfg: dict[str, Any]) -> list[DatasetInstance]:
    """Load samples from a JSON or JSONL file. Relative media paths are resolved
    against the data root if provided."""
    path = cfg["path"]
    root = cfg.get("root")
    if root and not os.path.isabs(path):
        path = os.path.join(root, path)
    samples: list[DatasetInstance] = []
    if path.endswith(".jsonl"):
        records: list[Any] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data if isinstance(data, list) else data.get("samples", data.get("data", [data]))
    for i, rec in enumerate(records):
        fields = dict(rec)
        for k, v in list(fields.items()):
            if isinstance(v, str) and (v.endswith((".jpg", ".jpeg", ".png", ".webp", ".wav", ".mp3"))):
                fields[k] = _resolve_media(v, root)
        samples.append(DatasetInstance(dataset_id=card.dataset_id, index=i, fields=fields))
    return samples


def load_directory(card: DatasetCard, cfg: dict[str, Any]) -> list[DatasetInstance]:
    """Load a folder of media files with an optional annotations JSON."""
    root = cfg["root"]
    ann_path = cfg.get("annotations")  # optional: filename -> fields
    ann: dict[str, dict[str, Any]] = {}
    if ann_path:
        with open(ann_path, "r", encoding="utf-8") as f:
            ann = json.load(f)
    samples: list[DatasetInstance] = []
    exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".wav", ".mp3", ".flac")
    media_field = cfg.get("media_field", "media_path")
    files = sorted(
        os.path.join(root, fn)
        for fn in os.listdir(root)
        if fn.lower().endswith(exts) and os.path.isfile(os.path.join(root, fn))
    )
    for i, fp in enumerate(files):
        fields = dict(ann.get(os.path.basename(fp), {}))
        fields[media_field] = fp
        samples.append(DatasetInstance(dataset_id=card.dataset_id, index=i, fields=fields))
    return samples
