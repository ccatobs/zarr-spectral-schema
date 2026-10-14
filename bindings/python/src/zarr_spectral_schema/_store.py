"""Read a Zarr v3 store into a normalized, language-neutral view.

This is the per-language store-reader seam: the *only* binding-specific code in
the validation path. It reads ``zarr.json`` metadata directly (no zarr library
dependency) so the Rust and Python readers produce the same normalized shape and
the generic interpreter on top of it cannot diverge between languages.

Object storage (#20). One input grammar in both bindings: an argument that
starts with ``scheme://`` is a URL, opened through fsspec
(``s3://bucket/store.zarr`` with s3fs installed), and its path is
percent-decoded. Anything else is a local path, taken as it is. The walk reads
only ``zarr.json`` objects: it lists the children of each group node and stops
at an array node, so it never lists or opens a chunk key. A directory without a
``zarr.json`` is not a node, and the local walk does not follow a symlinked
directory. An I/O failure is an error of its own, never a gap and never an
exception: a failed listing is ``'<rel>/': cannot list``, a failed read of a
``zarr.json`` is ``'<rel>': cannot read metadata``.

Parse rule (both bindings, stated in the spec): metadata is strict JSON. A
non-finite number (``NaN``, ``Infinity``, ``-Infinity``) is its own error; any
other unparseable ``zarr.json`` is a malformed-metadata error. The node is then
left out of the view. The reader never raises for a bad node.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import fsspec


@dataclass
class ArrayView:
    dtype: str
    # list of non-negative integers, or None when the metadata holds anything else
    shape: list[int] | None
    # zarr v3 `dimension_names` as parsed (None when absent or null)
    dimension_names: Any = None
    attrs: dict[str, Any] = field(default_factory=dict)

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


_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


def read_store(store_path: str | Path) -> StoreView:
    """Walk a Zarr v3 store and normalize every node keyed by relative path.

    ``store_path`` is a local path or a URL (``s3://bucket/store.zarr``).
    Raises ``ValueError`` only when the root is not a readable Zarr v3 group.
    """
    store = str(store_path)
    if _URL_RE.match(store):
        # A fresh instance per check: no stale listing, no stale endpoint.
        fs, root = fsspec.core.url_to_fs(store, skip_instance_cache=True, use_listings_cache=False)
        root = unquote(root)
    else:
        fs, root = fsspec.filesystem("file"), store
    root = root.rstrip("/")
    root_errors: list[str] = []
    root_meta = _read_node(fs, f"{root}/zarr.json", "zarr.json", root_errors)
    if root_errors:
        raise ValueError(root_errors[0])
    if root_meta is None or root_meta.get("node_type") != "group":
        raise ValueError(f"{store_path}: not a Zarr v3 group (missing/!group root zarr.json)")

    view = StoreView(root_attrs=_attributes(root_meta))
    _walk(fs, root, "", view)  # a root that cannot be listed raises ValueError
    return view


def _walk(fs, root: str, rel: str, view: StoreView) -> None:
    """Record each child node of the group at ``rel``; recurse into child groups.

    Raises ``ValueError`` when the group at ``rel`` cannot be listed; a child
    group that cannot be listed is an error in ``view.errors``.

    # ponytail: one request at a time; fetch siblings concurrently if a large
    # store on object storage makes the walk slow.
    """
    try:
        entries = fs.ls(f"{root}/{rel}" if rel else root, detail=True)
    except Exception:  # noqa: BLE001 - any filesystem failure; botocore errors are not OSError
        raise ValueError(f"'{rel}/': cannot list") from None
    names = sorted(
        e["name"].rstrip("/").rsplit("/", 1)[-1]
        for e in entries
        if e["type"] == "directory" and not e.get("islink")
    )
    for name in names:
        child = f"{rel}/{name}" if rel else name
        meta = _read_node(fs, f"{root}/{child}/zarr.json", f"{child}/zarr.json", view.errors)
        if meta is None:
            continue
        if meta.get("node_type") == "array":
            view.arrays[child] = ArrayView(
                dtype=_dtype_name(meta.get("data_type")),
                shape=_shape(meta.get("shape")),
                dimension_names=meta.get("dimension_names"),
                attrs=_attributes(meta),
            )
        elif meta.get("node_type") == "group":
            view.group_attrs[child] = _attributes(meta)
            try:
                _walk(fs, root, child, view)
            except ValueError as e:
                view.errors.append(str(e))


def _read_node(fs, path: str, rel: str, errors: list[str]) -> dict[str, Any] | None:
    """Parse one ``zarr.json``; ``None`` when absent; on failure append the error naming ``rel``."""
    try:
        raw = fs.cat_file(path)
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - any filesystem failure; botocore errors are not OSError
        errors.append(f"'{rel}': cannot read metadata")
        return None
    try:
        meta = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except _NonFinite:
        errors.append(f"'{rel}': non-finite number in metadata")
        return None
    # JSONDecodeError and UnicodeDecodeError are ValueErrors; RecursionError is
    # metadata nested past the parser's depth (serde_json stops at 128 levels).
    except (ValueError, RecursionError):
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


def _shape(shape: Any) -> list[int] | None:
    """A list of non-negative integers below 2**64 (what serde_json reads as
    u64), else None. bool is no integer here."""
    ok = isinstance(shape, list) and all(
        isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 1 << 64 for n in shape
    )
    return shape if ok else None


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
