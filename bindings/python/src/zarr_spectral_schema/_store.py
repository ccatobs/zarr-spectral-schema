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
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import fsspec


@dataclass
class ArrayView:
    """One array node of a store, normalized from its ``zarr.json``.

    Attributes
    ----------
    dtype
        The Zarr v3 data type name, for example ``"uint32"``. The string form and
        the object form of ``data_type`` give the same name.
    shape
        The array shape, or ``None`` when the metadata holds anything else than a
        list of non-negative integers.
    attrs
        The user attributes of the array, for example ``units``.
    dimension_names
        The Zarr v3 ``dimension_names`` as parsed. ``None`` when absent or null.
    fill_value
        The Zarr v3 ``fill_value`` as parsed. ``None`` when absent.
    ndim
        The number of dimensions, ``len(shape)``; ``None`` when ``shape`` is.
    """

    dtype: str
    # list of non-negative integers, or None when the metadata holds anything else
    shape: list[int] | None
    # The positional order of the first release with attrs: dtype, shape, attrs.
    attrs: dict[str, Any] = field(default_factory=dict)
    # zarr v3 `dimension_names` as parsed (None when absent or null)
    dimension_names: Any = None
    # zarr v3 `fill_value` as parsed (None when absent)
    fill_value: Any = None

    @property
    def ndim(self) -> int | None:
        return None if self.shape is None else len(self.shape)


@dataclass
class StoreView:
    """A store read into one normalized view; nodes are keyed by relative path.

    Attributes
    ----------
    root_attrs
        The attributes of the root group.
    group_attrs
        The attributes of each child group, keyed by path such as ``scan_000001/source``.
    arrays
        Each array as an :class:`ArrayView`, keyed by path such as ``scan_000001/source/data_5d``.
    errors
        Parse errors of non-root ``zarr.json`` files. A node that does not parse
        is left out of the view together with its subtree.
    unknown
        The paths of the nodes left out that way, and of each group that cannot
        be listed: the validator reports no node at or below them as missing.
    """

    root_attrs: dict[str, Any]
    # keyed by store-relative path, e.g. "source" / "source/data_5d"
    group_attrs: dict[str, dict[str, Any]] = field(default_factory=dict)
    arrays: dict[str, ArrayView] = field(default_factory=dict)
    # metadata parse errors of non-root nodes, e.g. "'scan_000001/zarr.json': ..."
    errors: list[str] = field(default_factory=list)
    unknown: set[str] = field(default_factory=set)


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
        try:
            fs, root = fsspec.core.url_to_fs(
                store, skip_instance_cache=True, use_listings_cache=False
            )
        except ImportError as e:  # the fsspec backend of the scheme is not installed
            raise ValueError(f"{store}: {e}") from None
        root = unquote(root)
    else:
        # Absolute first: fsspec would expand a leading ~ and strip a file:
        # prefix; the Rust binding takes the path as it is.
        fs, root = fsspec.filesystem("file"), os.path.abspath(store)
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
        failed = len(view.errors)
        meta = _read_node(fs, f"{root}/{child}/zarr.json", f"{child}/zarr.json", view.errors)
        if len(view.errors) > failed:
            view.unknown.add(child)
        if meta is None:
            continue
        if meta.get("node_type") == "array":
            view.arrays[child] = ArrayView(
                dtype=_dtype_name(meta.get("data_type")),
                shape=_shape(meta.get("shape")),
                dimension_names=meta.get("dimension_names"),
                attrs=_attributes(meta),
                fill_value=meta.get("fill_value"),
            )
        elif meta.get("node_type") == "group":
            view.group_attrs[child] = _attributes(meta)
            try:
                _walk(fs, root, child, view)
            except ValueError as e:
                view.errors.append(str(e))
                view.unknown.add(child)


def _read_node(fs, path: str, rel: str, errors: list[str]) -> dict[str, Any] | None:
    """Parse one ``zarr.json``; ``None`` when absent; on failure append the error naming ``rel``."""
    try:
        # start=0: one ranged GET on object storage, with no HEAD before it.
        raw = fs.cat_file(path, start=0)
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
    # metadata nested past the parser's depth.
    except (ValueError, RecursionError):
        meta = None
    if not isinstance(meta, dict) or _depth(meta) > _MAX_DEPTH:
        errors.append(f"'{rel}': malformed metadata (not valid JSON)")
        return None
    return meta


# serde_json, the parser of the Rust binding, reads at most 127 nested arrays
# and objects; deeper metadata is malformed in both bindings.
_MAX_DEPTH = 127


def _depth(value: Any) -> int:
    """The nesting depth of arrays and objects in a parsed JSON value."""
    depth, level = 0, [value]
    while level:
        depth += 1
        level = [
            v
            for c in level
            for v in (c.values() if isinstance(c, dict) else c)
            if isinstance(v, (dict, list))
        ]
    return depth


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
