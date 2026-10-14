"""The generic-interpreter validator.

Judgment-free: it walks the declarative spec and checks a normalized store view
against it. No field is special-cased — every rule comes from the spec — so the
Python and Rust validators implement the *same* interpretation and cannot drift.

Three checks today:

* **Level detection** (Slice 4, #5) — a store is L0 *or* L1, detected by which
  per-level ``detect`` root attr it carries (``l0_schema_version`` ⇒ L0,
  ``cal_schema_version`` ⇒ L1). A store declaring no known level is an error.
* **Structure** — the full L0+L1 contract, interpreted from the spec's
  ``placement``/``inline`` group fields and the per-level ``*_attrs`` sections.
  Root attrs, per-scan attrs, per-band attrs (multi-frontend), the scan-or-band
  arrays/attrs, the L0 ``source``/``calibration`` sub-groups, and the diagnostic
  ``skydiff``/``skychopdiff`` sub-groups are all resolved by the same generic
  walk — never hand-branched per field. The spec's ``one_of`` (exactly one of
  these groups per target), ``equals`` (attr value constraint), ``format``
  (``uri-reference``), ``enum`` (value set), ``sentinel`` (not-applicable
  value), ``prefix`` (array family), dtype sets and array ``attrs`` are
  interpreted the same way. Each array's ``dimension_names`` and ``units`` attr
  are checked against its ``dims``/``units``; arrays at one target agree on the
  length of each shared dimension; a multi-frontend scan group holds no
  scan-or-band attr. Every structure finding starts with ``[<level>] ``; version
  and level-detection errors do not.
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

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ._spec import load_spec
from ._store import StoreView, read_store

# Scan groups are ``scan_<6+ digits>`` (writer: ``scan_{:06}``); band sub-groups
# are ``band_<index>`` (writer: ``band_{band_index}``, base-0, no zero-pad), at L0
# and L1 alike. ASCII digits only, whole name
# (fullmatch), so both bindings accept the same names.
_SCAN_PATTERN = "^scan_[0-9]{6,}$"
_SCAN_RE = re.compile(_SCAN_PATTERN)
_BAND_RE = re.compile(r"band_[0-9]+")
_VERSION_RE = re.compile(r"[0-9]+(\.[0-9]+)?")
# RFC 3986 URI reference (ASCII; a % starts a two-hex-digit escape); a leading
# "/" (absolute path) is excluded: it names a file on one host only.
_URI_REFERENCE_RE = re.compile(r"(?!/)(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})+")

# Map a spec attr ``type`` to the Python types it may appear as in parsed JSON.
# `number` admits int; `integer` excludes bool (bool is an int subclass in JSON).
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _sentinel(attr_type: str) -> str | None:
    """The not-applicable sentinel of an attr type: "NONE" or JSON null."""
    return "NONE" if attr_type == "string" else None


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
    try:
        view = read_store(store_path)
    except ValueError as e:  # the root is not a readable group: nothing to walk
        return ValidationResult(errors=[str(e)])
    return check_view(view, spec)


def validate_view(view: StoreView, spec: dict) -> list[str]:
    """Errors-only view-level validation (back-compat)."""
    return check_view(view, spec).errors


def check_view(view: StoreView, spec: dict) -> ValidationResult:
    result = ValidationResult()

    levels = spec.get("levels", {})

    # ── level detection ────────────────────────────────────────────────────
    # A store is exactly one level, marked by which `detect` root attr it carries.
    applicable = {
        name: level
        for name, level in levels.items()
        if level.get("detect") and level["detect"] in view.root_attrs
    }
    if not applicable:
        detects = sorted(level["detect"] for level in levels.values() if level.get("detect"))
        result.errors.append(
            "store declares no known level: none of the detect attributes "
            f"{detects} are present in root attributes"
        )
        return result

    # Negotiate only the versions belonging to a detected level: an L0 store
    # carries l0_schema_version, an L1 store cal_schema_version — checking the
    # absent one would spuriously fail every single-level store.
    applicable_level_names = set(applicable)
    _negotiate_versions(view, spec, applicable_level_names, result)

    for level_name, level in applicable.items():
        _check_level(view, level_name, level, result)

    return result


def _check_level(view: StoreView, level_name: str, level: dict, result: ValidationResult) -> None:
    """Walk one applicable level's structural contract against the store view."""
    # Metadata that did not parse is a structure finding of this level.
    result.errors.extend(f"[{level_name}] {e}" for e in view.errors)

    # Root attrs declared for this level.
    _check_attrs(view.root_attrs, level.get("root_attrs", {}), "root", level_name, result)

    groups = level.get("groups", {})

    # `scan` groups: arrays/attrs placed relative to each scan group. We only
    # require scan groups when the level actually declares scan-relative content.
    needs_scans = bool(
        level.get("scan_attrs")
        or level.get("band_attrs")
        or level.get("scan_or_band_attrs")
        or groups
    )
    root_children = {p for p in (*view.group_attrs, *view.arrays) if "/" not in p}
    for name in sorted(root_children):
        if name.startswith("scan_") and not _SCAN_RE.fullmatch(name):
            result.errors.append(
                f"[{level_name}] root child '{name}' does not match the scan pattern "
                f"{_SCAN_PATTERN}"
            )
    scan_groups = sorted(p for p in view.group_attrs if _SCAN_RE.fullmatch(p))
    if needs_scans and not scan_groups:
        result.errors.append(f"[{level_name}] no scan_<NNNNNN> groups found at the store root")
        return

    for sg in scan_groups:
        bands = sorted(
            p
            for p in view.group_attrs
            if p.startswith(f"{sg}/") and _BAND_RE.fullmatch(p[len(sg) + 1 :])
        )

        # Per-scan-group attrs always checked at the scan group itself.
        _check_attrs(
            view.group_attrs[sg],
            level.get("scan_attrs", {}),
            sg,
            level_name,
            result,
        )

        # Multi-frontend: per-band attrs on each band group, and no scan-or-band
        # attr on the scan group (it belongs on each band).
        for b in bands:
            _check_attrs(view.group_attrs[b], level.get("band_attrs", {}), b, level_name, result)
        for name in level.get("scan_or_band_attrs", {}) if bands else ():
            if name in view.group_attrs[sg]:
                result.errors.append(
                    f"[{level_name}] attribute '{name}' at '{sg}' belongs on each band "
                    "of a multi-frontend scan, not on the scan group"
                )

        # `scan` placement resolves to the scan group; `scan_or_band` to the
        # scan group (single-frontend) or each band (multi-frontend).
        targets = {"scan": [sg], "scan_or_band": bands or [sg]}
        # (path, dims, shape) of each array checked, per target.
        seen: dict[str, list[tuple[str, list[str], list[int]]]] = {}
        for t in targets["scan_or_band"]:
            _check_attrs(
                view.group_attrs[t],
                level.get("scan_or_band_attrs", {}),
                t,
                level_name,
                result,
            )
        for placement, where in targets.items():
            placed = {n: g for n, g in groups.items() if g.get("placement") == placement}
            one_of = [n for n in level.get("one_of", []) if n in placed]
            for t in where:
                present = [n for n in one_of if f"{t}/{n}" in view.group_attrs]
                if one_of and len(present) != 1:
                    result.errors.append(
                        f"[{level_name}] '{t}' must hold exactly one of {one_of}, found {present}"
                    )
                for gname, group in placed.items():
                    if gname in one_of and gname not in present:
                        continue
                    _check_group_arrays(
                        view, t, gname, group, level_name, result, seen.setdefault(t, [])
                    )
        for t, arrays in seen.items():
            _check_shared_dims(t, arrays, level_name, result)


def _check_group_arrays(
    view: StoreView,
    base: str,
    group_name: str,
    group: dict,
    level_name: str,
    result: ValidationResult,
    seen: list[tuple[str, list[str], list[int]]],
) -> None:
    """Check one group's arrays under ``base``.

    ``inline`` groups place arrays directly in ``base``; otherwise they sit under
    ``base/<group_name>/``. Required arrays must exist (dtype + ndim checked);
    optional arrays are checked only when present. An array family (``prefix``)
    checks every array whose name starts with the prefix.
    """
    inline = group.get("inline", False)
    prefix = base if inline else f"{base}/{group_name}"
    for array_name, aspec in group.get("arrays", {}).items():
        if "prefix" not in aspec:
            _check_array(view, f"{prefix}/{array_name}", aspec, level_name, result, seen)
            continue
        start = f"{prefix}/{aspec['prefix']}"
        members = sorted(
            r
            for r in (*view.arrays, *view.group_attrs)
            if r.startswith(start) and "/" not in r[len(prefix) + 1 :]
        )
        if not members and aspec.get("required", False):
            result.errors.append(f"[{level_name}] required array family '{start}*' is missing")
        for rel in members:
            _check_array(view, rel, aspec, level_name, result, seen)


def _check_array(
    view: StoreView,
    rel: str,
    aspec: dict,
    level_name: str,
    result: ValidationResult,
    seen: list[tuple[str, list[str], list[int]]],
) -> None:
    """Check one array path: node type, presence (if required), dtype, ndim,
    ``dimension_names`` against the spec's ``dims``, the ``units`` attr and the
    array's own ``attrs``."""
    if rel in view.group_attrs:
        result.errors.append(f"[{level_name}] '{rel}' is a group, expected an array")
        return
    arr = view.arrays.get(rel)
    if arr is None:
        if aspec.get("required", False):
            result.errors.append(f"[{level_name}] required array '{rel}' is missing")
        return
    allowed = aspec["dtype"] if isinstance(aspec["dtype"], list) else [aspec["dtype"]]
    if arr.dtype not in allowed:
        result.errors.append(
            f"[{level_name}] array '{rel}' dtype {arr.dtype!r} != expected {aspec['dtype']!r}"
        )
    if arr.shape is None:
        result.errors.append(
            f"[{level_name}] array '{rel}' shape is not a list of non-negative integers"
        )
    elif arr.ndim != aspec["ndim"]:
        result.errors.append(
            f"[{level_name}] array '{rel}' ndim {arr.ndim} != expected {aspec['ndim']}"
        )
    elif arr.ndim == len(aspec["dims"]):
        seen.append((rel, aspec["dims"], arr.shape))
    dims = json.dumps(aspec["dims"])
    if arr.dimension_names is None:
        result.errors.append(
            f"[{level_name}] array '{rel}' has no dimension_names (expected {dims})"
        )
    elif arr.dimension_names != aspec["dims"]:
        result.errors.append(
            f"[{level_name}] array '{rel}' dimension_names "
            f"{json.dumps(arr.dimension_names)} != expected {dims}"
        )
    if "units" in aspec:
        units = json.dumps(aspec["units"])
        if "units" not in arr.attrs:
            result.errors.append(
                f"[{level_name}] array '{rel}' has no units attr (expected {units})"
            )
        elif arr.attrs["units"] != aspec["units"]:
            result.errors.append(
                f"[{level_name}] array '{rel}' units {json.dumps(arr.attrs['units'])} "
                f"!= expected {units}"
            )
    _check_attrs(arr.attrs, aspec.get("attrs", {}), rel, level_name, result)


def _check_shared_dims(
    target: str,
    arrays: list[tuple[str, list[str], list[int]]],
    level_name: str,
    result: ValidationResult,
) -> None:
    """Arrays at one target that share an axis name agree on its length."""
    lengths: dict[str, dict[int, list[str]]] = {}
    for rel, dims, shape in arrays:
        for dim, n in zip(dims, shape):
            lengths.setdefault(dim, {}).setdefault(n, []).append(rel[len(target) + 1 :])
    for dim in sorted(lengths):
        if len(lengths[dim]) > 1:
            parts = "; ".join(
                f"{n} in [{', '.join(sorted(names))}]" for n, names in sorted(lengths[dim].items())
            )
            result.errors.append(
                f"[{level_name}] arrays at '{target}' disagree on the length of "
                f"dimension '{dim}': {parts}"
            )


def _check_attrs(
    attrs: dict,
    declared: dict,
    where: str,
    level_name: str,
    result: ValidationResult,
) -> None:
    """Check declared attrs at one placement location.

    Required attrs must be present and (if present) type-checked; optional attrs
    are type-checked only when present. An attr with ``sentinel: true`` may hold
    the not-applicable sentinel instead. An ``enum`` value set, an ``equals``
    value constraint and a ``format`` are checked after the type. ``where`` is
    the store path used in
    messages (``root`` or a group path) so failures point at the exact location.
    """
    for name, aspec in declared.items():
        present = name in attrs
        if not present:
            if aspec.get("required", False):
                result.errors.append(
                    f"[{level_name}] required attribute '{name}' is missing at '{where}'"
                )
            continue
        expected = aspec["type"]
        if aspec.get("sentinel") and attrs[name] == _sentinel(expected):
            continue
        checker = _TYPE_CHECKS.get(expected)
        if checker is not None and not checker(attrs[name]):
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must be of type "
                f"{expected!r}, got {attrs[name]!r}"
            )
        elif "enum" in aspec and attrs[name] not in aspec["enum"]:
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must be one of "
                f"{json.dumps(aspec['enum'])}, got {json.dumps(attrs[name])}"
            )
        elif "equals" in aspec and attrs[name] != aspec["equals"]:
            # JSON rendering keeps the message identical to the Rust binding's.
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must equal "
                f"{json.dumps(aspec['equals'])}, got {json.dumps(attrs[name])}"
            )
        elif aspec.get("format") == "uri-reference" and not _URI_REFERENCE_RE.fullmatch(
            attrs[name]
        ):
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must be a URI reference "
                f"(RFC 3986) and not an absolute path, got "
                f"{json.dumps(attrs[name], ensure_ascii=False)}"
            )


def _negotiate_versions(
    view: StoreView, spec: dict, levels: set[str], result: ValidationResult
) -> None:
    """Presence/encoding check + two-axis range negotiation for on-disk versions.

    Only versions whose ``level`` is in ``levels`` (the detected level(s)) are
    negotiated, so a single-level store is not penalised for lacking the other
    level's version attr.
    """
    for attr_name, vspec in spec.get("versions", {}).items():
        if vspec.get("level") not in levels:
            continue
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
        msg = (
            f"root attribute '{attr_name}': store version {disk} is older than the minimum "
            f"supported ({vspec['min_supported']})"
        )
        return ([msg], [])
    if store[0] > current[0]:
        msg = (
            f"root attribute '{attr_name}': store version {disk} is a newer major than this "
            f"build supports ({vspec['value']}); refusing to validate"
        )
        return ([msg], [])
    if store[0] == current[0] and store[1] > current[1]:
        msg = (
            f"root attribute '{attr_name}': store version {disk} is a newer minor than this "
            f"build knows ({vspec['value']}); additive new fields are ignored"
        )
        return ([], [msg])
    return ([], [])


def _parse_version(value: object) -> tuple[int, int] | None:
    """Parse ``major[.minor]`` (or a bare integer) into a ``(major, minor)`` tuple.

    ASCII digits only (``int`` alone would take Unicode digits, signs, spaces and
    underscores), the same grammar as the meta-schema and the Rust binding.
    """
    text = str(value)
    if not _VERSION_RE.fullmatch(text):
        return None
    major, _, minor = text.partition(".")
    return (int(major), int(minor or 0))


def _is_int(value: object) -> bool:
    # bool is a subclass of int in Python; a version must not be True/False.
    return isinstance(value, int) and not isinstance(value, bool)
