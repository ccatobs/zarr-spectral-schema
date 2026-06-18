"""The generic-interpreter validator.

Judgment-free: it walks the declarative spec and checks a normalized store view
against it. No field is special-cased — every rule comes from the spec — so the
Python and Rust validators implement the *same* interpretation and cannot drift.

Two checks today:

* **Structure** — presence + dtype/ndim of declared required arrays.
* **Version negotiation** (Slice 3, #4) — each producer-owned on-disk version
  (``l0_schema_version`` integer, ``cal_schema_version`` ``major.minor``) is
  range-checked against the spec's supported window (``min_supported`` ..
  ``value``): below the floor or a newer *major* is an **error**; a newer *minor*
  is a single **warning** (additive — unknown fields are ignored, validation
  still passes); older-or-equal within the window passes silently.

``validate`` returns errors only (empty == conformant) for back-compat; ``check``
returns the full :class:`ValidationResult` with warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ._spec import load_spec
from ._store import StoreView, read_store


@dataclass
class ValidationResult:
    """Outcome of validating a store: hard ``errors`` and advisory ``warnings``."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the store is conformant — warnings do not make it fail."""
        return not self.errors


def validate(store_path: str | Path) -> list[str]:
    """Validate a store; return errors only (empty == conformant).

    Back-compat surface. Use :func:`check` to also surface newer-minor warnings.
    """
    return check(store_path).errors


def check(store_path: str | Path) -> ValidationResult:
    """Validate a store against the spec, returning errors and warnings."""
    spec = load_spec()
    view = read_store(store_path)
    return check_view(view, spec)


def validate_view(view: StoreView, spec: dict) -> list[str]:
    """Errors-only view-level validation (back-compat)."""
    return check_view(view, spec).errors


def check_view(view: StoreView, spec: dict) -> ValidationResult:
    result = ValidationResult()

    # Version attributes live in root attrs (store-and-forward identity travels
    # at root in v1). Check presence, on-disk encoding, then range-negotiate.
    for attr_name, vspec in spec.get("versions", {}).items():
        if attr_name not in view.root_attrs:
            result.errors.append(f"root attribute '{attr_name}' is required but missing")
            continue
        value = view.root_attrs[attr_name]
        on_disk = vspec["on_disk"]
        if on_disk == "integer" and not _is_int(value):
            result.errors.append(
                f"root attribute '{attr_name}' must be an integer on disk, got {value!r}"
            )
            continue
        if on_disk == "string" and not isinstance(value, str):
            result.errors.append(
                f"root attribute '{attr_name}' must be a string on disk, got {value!r}"
            )
            continue
        errors, warnings = _negotiate(attr_name, vspec, value)
        result.errors.extend(errors)
        result.warnings.extend(warnings)

    # Structural arrays. In v1, a group at level L maps to the store-relative
    # path '<group>' and its arrays to '<group>/<array>'.
    for level_name, level in spec.get("levels", {}).items():
        for group_name, group in level.get("groups", {}).items():
            for array_name, aspec in group.get("arrays", {}).items():
                if not aspec.get("required", False):
                    continue
                rel = f"{group_name}/{array_name}"
                arr = view.arrays.get(rel)
                if arr is None:
                    result.errors.append(f"[{level_name}] required array '{rel}' is missing")
                    continue
                if arr.dtype != aspec["dtype"]:
                    result.errors.append(
                        f"[{level_name}] array '{rel}' dtype {arr.dtype!r} != expected {aspec['dtype']!r}"
                    )
                if arr.ndim != aspec["ndim"]:
                    result.errors.append(
                        f"[{level_name}] array '{rel}' ndim {arr.ndim} != expected {aspec['ndim']}"
                    )
    return result


def _negotiate(attr_name: str, vspec: dict, value: object) -> tuple[list[str], list[str]]:
    """Range-negotiate one on-disk version against the spec's supported window.

    Returns ``(errors, warnings)``. The two axes are major (breaking → error) and
    minor (additive → warn). An integer version (no minor) only exercises major.
    """
    store = _parse_version(value)
    if store is None:
        return ([f"root attribute '{attr_name}': cannot parse version {value!r}"], [])
    current = _parse_version(vspec["value"])
    floor = _parse_version(vspec["min_supported"])
    assert current is not None and floor is not None  # guaranteed well-formed by the meta-schema

    disk = str(value)
    if store < floor:
        return (
            [
                f"root attribute '{attr_name}': store version {disk} is older than the minimum "
                f"supported ({vspec['min_supported']})"
            ],
            [],
        )
    if store[0] > current[0]:
        return (
            [
                f"root attribute '{attr_name}': store version {disk} is a newer major than this "
                f"build supports ({vspec['value']}); refusing to validate"
            ],
            [],
        )
    if store[0] == current[0] and store[1] > current[1]:
        return (
            [],
            [
                f"root attribute '{attr_name}': store version {disk} is a newer minor than this "
                f"build knows ({vspec['value']}); additive new fields are ignored"
            ],
        )
    return ([], [])


def _parse_version(value: object) -> tuple[int, int] | None:
    """Parse ``major[.minor]`` (or a bare integer) into a ``(major, minor)`` tuple."""
    parts = str(value).strip().split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    return (major, minor)


def _is_int(value: object) -> bool:
    # bool is a subclass of int in Python; a version must not be True/False.
    return isinstance(value, int) and not isinstance(value, bool)
