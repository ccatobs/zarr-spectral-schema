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
from ._validator import ValidationResult, check, validate, validate_view

__all__ = ["ValidationResult", "check", "constants", "load_spec", "validate", "validate_view"]


def _build_constants() -> dict[str, str | tuple[str, ...]]:
    """Spec-derived constants, mirroring the Rust binding's generated consts.

    Same identifier rules as build.rs: array → UPPER(on-disk name); group →
    UPPER(name)_GROUP (or none if ``const: false``); version → UPPER(attr key);
    any node may override via ``const``. ``lists`` → tuples; ``markers`` → str.
    """
    spec = load_spec()
    consts: dict[str, str | tuple[str, ...]] = {}

    def put(name: str, value: str | tuple[str, ...]) -> None:
        if name in consts and consts[name] != value:
            raise ValueError(f"const {name} declared with conflicting values")
        consts[name] = value

    for attr_name, vspec in spec.get("versions", {}).items():
        put(vspec.get("const") or attr_name.upper(), vspec["value"])

    for level in spec.get("levels", {}).values():
        for group_name, group in level.get("groups", {}).items():
            gconst = group.get("const")
            if gconst is not False:
                put(gconst or f"{group_name.upper()}_GROUP", group_name)
            for array_name, aspec in group.get("arrays", {}).items():
                put(aspec.get("const") or array_name.upper(), array_name)

    for name, value in spec.get("markers", {}).items():
        put(name, value)

    for name, items in spec.get("lists", {}).items():
        put(name, tuple(items))

    return consts


# Spec-derived constants exposed both as a dict and as module attributes.
constants: dict[str, str | tuple[str, ...]] = _build_constants()
globals().update(constants)
__all__ += list(constants)
