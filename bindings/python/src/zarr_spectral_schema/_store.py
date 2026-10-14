"""Read a Zarr v3 store into a normalized, language-neutral view.

This is the per-language store-reader seam: the *only* binding-specific code in
the validation path. It reads ``zarr.json`` metadata directly (no zarr library
dependency) so the Rust and Python readers produce the same normalized shape and
the generic interpreter on top of it cannot diverge between languages.

Parse rule (both bindings, stated in the spec): metadata is strict JSON. A
non-finite number (``NaN``, ``Infinity``, ``-Infinity``) is its own error; any
other unparseable ``zarr.json`` is a malformed-metadata error. The node is then
left out of the view. The reader never raises for a bad node.
"""

from __future__ import annotations

import json
import math
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
    # metadata parse errors of non-root nodes, e.g. "'scan_000001/zarr.json': ..."
    errors: list[str] = field(default_factory=list)


class _NonFinite(Exception):
    pass


def read_store(store_path: str | Path) -> StoreView:
    """Walk a Zarr v3 store and normalize every node keyed by relative path.

    Raises ``ValueError`` only when the root is not a readable Zarr v3 group.
    """
    root = Path(store_path)
    root_errors: list[str] = []
    root_meta = _read_node(root / "zarr.json", "zarr.json", root_errors)
    if root_errors:
        raise ValueError(root_errors[0])
    if root_meta is None or root_meta.get("node_type") != "group":
        raise ValueError(f"{store_path}: not a Zarr v3 group (missing/!group root zarr.json)")

    view = StoreView(root_attrs=_attributes(root_meta))
    for meta_path in sorted(root.rglob("zarr.json")):
        if meta_path.parent == root:
            continue  # root already handled
        rel = meta_path.parent.relative_to(root).as_posix()
        meta = _read_node(meta_path, f"{rel}/zarr.json", view.errors)
        if meta is None:
            continue
        if meta.get("node_type") == "array":
            shape = meta.get("shape")
            view.arrays[rel] = ArrayView(
                dtype=_dtype_name(meta.get("data_type")),
                shape=shape if isinstance(shape, list) else [],
            )
        elif meta.get("node_type") == "group":
            view.group_attrs[rel] = _attributes(meta)
    return view


def _read_node(path: Path, rel: str, errors: list[str]) -> dict[str, Any] | None:
    """Parse one ``zarr.json``; on failure append the error naming ``rel``."""
    if not path.is_file():
        return None
    try:
        meta = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except _NonFinite:
        errors.append(f"'{rel}': non-finite number in metadata")
        return None
    # JSONDecodeError and UnicodeDecodeError are ValueErrors; RecursionError is
    # metadata nested past the parser's depth (serde_json stops at 128 levels).
    except (ValueError, OSError, RecursionError):
        meta = None
    if not isinstance(meta, dict):
        errors.append(f"'{rel}': malformed metadata (not valid JSON)")
        return None
    return meta


def _reject_non_finite(token: str) -> float:
    raise _NonFinite(token)


def _finite_float(text: str) -> float:
    # An overflowing literal (1e400) is out of range, as in serde_json.
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"number out of range: {text}")
    return value


def _attributes(meta: dict[str, Any]) -> dict[str, Any]:
    attrs = meta.get("attributes", {})
    return attrs if isinstance(attrs, dict) else {}


def _dtype_name(data_type: Any) -> str:
    """Normalize a Zarr v3 ``data_type`` to its plain name.

    Core dtypes serialize as a bare string ("float32"); extension dtypes use an
    object with a "name" field. Anything else has no name ("").
    """
    if isinstance(data_type, str):
        return data_type
    if isinstance(data_type, dict) and isinstance(data_type.get("name"), str):
        return data_type["name"]
    return ""
