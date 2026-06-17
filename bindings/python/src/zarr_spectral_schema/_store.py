"""Read a Zarr v3 store into a normalized, language-neutral view.

This is the per-language store-reader seam: the *only* binding-specific code in
the validation path. It reads ``zarr.json`` metadata directly (no zarr library
dependency) so the Rust and Python readers produce the same normalized shape and
the generic interpreter on top of it cannot diverge between languages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArrayView:
    dtype: str
    shape: list[int]

    @property
    def ndim(self) -> int:
        return len(self.shape)


@dataclass
class StoreView:
    root_attrs: dict[str, Any]
    # keyed by store-relative path, e.g. "source" / "source/data_5d"
    group_attrs: dict[str, dict[str, Any]] = field(default_factory=dict)
    arrays: dict[str, ArrayView] = field(default_factory=dict)


def read_store(store_path: str | Path) -> StoreView:
    """Walk a Zarr v3 store and normalize every node keyed by relative path."""
    root = Path(store_path)
    root_meta = _read_node(root / "zarr.json")
    if root_meta is None or root_meta.get("node_type") != "group":
        raise ValueError(f"{store_path}: not a Zarr v3 group (missing/!group root zarr.json)")

    view = StoreView(root_attrs=root_meta.get("attributes", {}))
    for meta_path in sorted(root.rglob("zarr.json")):
        if meta_path.parent == root:
            continue  # root already handled
        rel = meta_path.parent.relative_to(root).as_posix()
        meta = _read_node(meta_path)
        if meta is None:
            continue
        if meta.get("node_type") == "array":
            view.arrays[rel] = ArrayView(
                dtype=_dtype_name(meta.get("data_type")),
                shape=list(meta.get("shape", [])),
            )
        elif meta.get("node_type") == "group":
            view.group_attrs[rel] = meta.get("attributes", {})
    return view


def _read_node(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _dtype_name(data_type: Any) -> str:
    """Normalize a Zarr v3 ``data_type`` to its plain name.

    Core dtypes serialize as a bare string ("float32"); extension dtypes use an
    object with a "name" field. We only need the name for contract checks.
    """
    if isinstance(data_type, str):
        return data_type
    if isinstance(data_type, dict):
        return str(data_type.get("name", data_type))
    return str(data_type)
