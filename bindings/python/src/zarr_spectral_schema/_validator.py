"""The generic-interpreter validator.

Judgment-free: it walks the declarative spec and checks a normalized store view
against it. No field is special-cased — every rule comes from the spec — so the
Python and Rust validators implement the *same* interpretation and cannot drift.

Slice 1 (tracer) scope: presence + on-disk type of declared versions, and
presence + dtype/ndim of declared required arrays. Version `>= min_supported`
negotiation (warn-minor / error-major) lands in Slice 3 (#4).
"""

from __future__ import annotations

from pathlib import Path

from ._spec import load_spec
from ._store import StoreView, read_store


def validate(store_path: str | Path) -> list[str]:
    """Validate a store against the spec. Returns a list of problems; empty = conformant."""
    spec = load_spec()
    view = read_store(store_path)
    return validate_view(view, spec)


def validate_view(view: StoreView, spec: dict) -> list[str]:
    problems: list[str] = []

    # Version attributes live in root attrs (store-and-forward identity travels
    # at root in v1). Check presence + on-disk encoding.
    for attr_name, vspec in spec.get("versions", {}).items():
        if attr_name not in view.root_attrs:
            problems.append(f"root attribute '{attr_name}' is required but missing")
            continue
        value = view.root_attrs[attr_name]
        if vspec["on_disk"] == "integer" and not _is_int(value):
            problems.append(f"root attribute '{attr_name}' must be an integer on disk, got {value!r}")
        elif vspec["on_disk"] == "string" and not isinstance(value, str):
            problems.append(f"root attribute '{attr_name}' must be a string on disk, got {value!r}")

    # Structural arrays. In the tracer, a group at level L maps to the store-
    # relative path '<group>' and its arrays to '<group>/<array>'.
    for level_name, level in spec.get("levels", {}).items():
        for group_name, group in level.get("groups", {}).items():
            for array_name, aspec in group.get("arrays", {}).items():
                if not aspec.get("required", False):
                    continue
                rel = f"{group_name}/{array_name}"
                arr = view.arrays.get(rel)
                if arr is None:
                    problems.append(f"[{level_name}] required array '{rel}' is missing")
                    continue
                if arr.dtype != aspec["dtype"]:
                    problems.append(
                        f"[{level_name}] array '{rel}' dtype {arr.dtype!r} != expected {aspec['dtype']!r}"
                    )
                if arr.ndim != aspec["ndim"]:
                    problems.append(
                        f"[{level_name}] array '{rel}' ndim {arr.ndim} != expected {aspec['ndim']}"
                    )
    return problems


def _is_int(value: object) -> bool:
    # bool is a subclass of int in Python; a version must not be True/False.
    return isinstance(value, int) and not isinstance(value, bool)
