"""zarr-spectral-schema — Python binding.

The on-disk L0–L1 Zarr contract, derived from the spec (``spec/schema.yaml``).
Constants are *loaded* from the spec at import (no hand-typed duplicate); the
``validate`` entry point runs the generic interpreter against a store.

The constant names mirror the Rust binding (and the cal-schema names it
replaces) so the two bindings expose one vocabulary: array key ``data_5d`` →
``DATA_5D``; version key ``l0_schema_version`` → ``L0_SCHEMA_VERSION``.
"""

from __future__ import annotations

from ._spec import load_spec
from ._validator import validate, validate_view

__all__ = ["validate", "validate_view", "load_spec", "constants"]


def _build_constants() -> dict[str, str]:
    spec = load_spec()
    consts: dict[str, str] = {}
    for version_name, vspec in spec.get("versions", {}).items():
        consts[version_name.upper()] = vspec["value"]
    for level in spec.get("levels", {}).values():
        for group in level.get("groups", {}).values():
            for array_name in group.get("arrays", {}):
                consts[array_name.upper()] = array_name
    return consts


# Spec-derived constants exposed both as a dict and as module attributes.
constants: dict[str, str] = _build_constants()
globals().update(constants)
__all__ += list(constants)
