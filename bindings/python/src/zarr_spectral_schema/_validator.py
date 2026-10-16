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
  value), ``prefix`` (array family), dtype sets, ``fill_value`` and array
  ``attrs`` are
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
# Up to L0 2, frontend b of scan N was a scan of its own, numbered
# N + 1000000 x b (ADR 0011, the band layout); a leftover is a finding.
_VIRTUAL_OFFSET = 1_000_000
_VERSION_RE = re.compile(r"[0-9]+(\.[0-9]+)?")
# RFC 3986 URI reference (ASCII; a % starts a two-hex-digit escape); a leading
# "/" (absolute path) and a file: URI are excluded: they name a file on one host.
_URI_REFERENCE_RE = re.compile(
    r"(?!/)(?![Ff][Ii][Ll][Ee]:)(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})+"
)


def _j(value: object) -> str:
    """A value as JSON (non-ASCII unescaped, keys sorted), as the Rust binding renders it."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# Spec sections are walked in sorted key order, as the Rust binding's BTreeMaps
# are, so both bindings report the same findings in the same order.

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
    """Validate a store; return errors only (empty list == conformant).

    Back-compat surface. Use :func:`check` to also surface newer-minor warnings.

    Parameters
    ----------
    store_path
        Local path or URL (``s3://bucket/store.zarr``) of a Zarr v3 store. An
        ``s3://`` URL needs the ``s3`` extra and the ``AWS_*`` environment variables.

    Returns
    -------
    list[str]
        The errors of :func:`check`. A structure finding starts with ``[L0] `` or
        ``[L1] ``; a version error and a level-detection error have no prefix.
        Never raises for a bad store: an unreadable root is returned as one error.
    """
    return check(store_path).errors


def check(store_path: str | Path) -> ValidationResult:
    """Validate a store against the spec, returning errors and warnings.

    The validator reads ``zarr.json`` objects only, never a chunk. It accepts the
    current major version of each level (L0 3, L1 2.0). A store below
    ``min_supported`` gets one version error, without a prefix. Structure
    findings follow when the store lacks fields of the current version; a
    consumer stops on the version error.

    Parameters
    ----------
    store_path
        Local path or URL (``s3://bucket/store.zarr``) of a Zarr v3 store. An
        ``s3://`` URL needs the ``s3`` extra and takes its endpoint, region and
        credentials from the standard ``AWS_*`` environment variables.

    Returns
    -------
    ValidationResult
        ``errors``: structure findings (prefix ``[L0] `` or ``[L1] ``), version
        errors and level-detection errors (no prefix). ``warnings``: a newer
        minor version. A metadata file that does not parse is an error, not an
        exception. An unreadable root group gives one error and nothing else.
    """
    spec = load_spec()
    try:
        view = read_store(store_path)
    except ValueError as e:  # the root is not a readable group: nothing to walk
        return ValidationResult(errors=[str(e)])
    return check_view(view, spec)


def validate_view(view: StoreView, spec: dict) -> list[str]:
    """Errors-only validation of a store view that :func:`read_store` built.

    Parameters
    ----------
    view
        The normalized store view.
    spec
        The loaded spec, from :func:`load_spec`.

    Returns
    -------
    list[str]
        The errors of :func:`check_view`.
    """
    return check_view(view, spec).errors


def check_view(view: StoreView, spec: dict) -> ValidationResult:
    """Validate a store view against the spec, returning errors and warnings.

    Parameters
    ----------
    view
        The normalized store view.
    spec
        The loaded spec, from :func:`load_spec`.

    Returns
    -------
    ValidationResult
        The same result as :func:`check` for the store the view came from.
    """
    result = ValidationResult()

    levels = spec.get("levels", {})

    # ── level detection ────────────────────────────────────────────────────
    # A store is exactly one level, marked by which `detect` root attr it carries.
    applicable = {
        name: level
        for name, level in sorted(levels.items())
        if level.get("detect") and level["detect"] in view.root_attrs
    }
    if not applicable:
        detects = sorted(level["detect"] for level in levels.values() if level.get("detect"))
        result.errors.append(
            "store declares no known level: none of the detect attributes "
            f"{_j(detects)} are present in root attributes"
        )
        return result

    # Negotiate only the versions belonging to a detected level: an L0 store
    # carries l0_schema_version, an L1 store cal_schema_version — checking the
    # absent one would spuriously fail every single-level store.
    applicable_level_names = set(applicable)
    _negotiate_versions(view, spec, applicable_level_names, result)

    # Metadata that did not parse is a structure finding, reported once (under
    # the first detected level).
    result.errors.extend(f"[{next(iter(applicable))}] {e}" for e in view.errors)
    children = _children(view)
    for level_name, level in applicable.items():
        _check_level(view, children, level_name, level, result)

    return result


def _children(view: StoreView) -> dict[str, list[str]]:
    """The paths of the child nodes of each node, sorted (``""`` is the root).

    One index for the whole check, so each lookup costs the children of one
    node, not the nodes of the store.
    """
    children: dict[str, list[str]] = {}
    for path in (*view.group_attrs, *view.arrays):
        children.setdefault(path.rpartition("/")[0], []).append(path)
    for paths in children.values():
        paths.sort()
    return children


def _check_level(
    view: StoreView,
    children: dict[str, list[str]],
    level_name: str,
    level: dict,
    result: ValidationResult,
) -> None:
    """Walk one applicable level's structural contract against the store view."""
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
    for name in children.get("", []):
        if name.startswith("scan_") and not _SCAN_RE.fullmatch(name):
            result.errors.append(
                f"[{level_name}] root child '{name}' does not match the scan pattern "
                f"{_SCAN_PATTERN}"
            )
        elif name in view.arrays and _SCAN_RE.fullmatch(name):
            result.errors.append(f"[{level_name}] '{name}' is an array, expected a scan group")
        # ponytail: at most 19 digits, the u64 range of the Rust binding's parse.
        elif _SCAN_RE.fullmatch(name) and len(name) <= 24 and int(name[5:]) >= _VIRTUAL_OFFSET:
            base = f"scan_{int(name[5:]) % _VIRTUAL_OFFSET:06d}"
            if base in view.group_attrs:
                result.errors.append(
                    f"[{level_name}] '{name}' is a virtual scan number ({base} + "
                    f"{_VIRTUAL_OFFSET} x {int(name[5:]) // _VIRTUAL_OFFSET}): a "
                    "multi-frontend scan holds each band under scan_N/band_B/"
                )
    scan_groups = [
        p for p in children.get("", []) if p in view.group_attrs and _SCAN_RE.fullmatch(p)
    ]
    if needs_scans and not scan_groups:
        result.errors.append(f"[{level_name}] no scan_<NNNNNN> groups found at the store root")
        return

    placed = {n: g for n, g in sorted(groups.items())}
    one_of = [n for n in level.get("one_of", []) if n in placed]
    for sg in scan_groups:
        bands = [
            p
            for p in children.get(sg, [])
            if p in view.group_attrs and _BAND_RE.fullmatch(p[len(sg) + 1 :])
        ]

        # Per-scan-group attrs always checked at the scan group itself.
        _check_attrs(
            view.group_attrs[sg],
            level.get("scan_attrs", {}),
            sg,
            level_name,
            result,
        )

        # Multi-frontend: per-band attrs on each band group; the scan group
        # holds band groups only, and no scan-or-band attr (it belongs on each
        # band).
        for b in bands:
            _check_attrs(view.group_attrs[b], level.get("band_attrs", {}), b, level_name, result)
        for node in children.get(sg, []) if bands else ():
            if node not in bands:
                result.errors.append(
                    f"[{level_name}] '{node}' is not a band group: the scan group of a "
                    "multi-frontend scan holds band groups only"
                )
        for name in sorted(level.get("scan_or_band_attrs", {})) if bands else ():
            if name in view.group_attrs[sg]:
                result.errors.append(
                    f"[{level_name}] attribute '{name}' at '{sg}' belongs on each band "
                    "of a multi-frontend scan, not on the scan group"
                )

        # `scan_or_band` placement resolves to the scan group (single-frontend)
        # or each band (multi-frontend).
        for t in bands or [sg]:
            _check_attrs(
                view.group_attrs[t],
                level.get("scan_or_band_attrs", {}),
                t,
                level_name,
                result,
            )
            # (path, dims, shape) of each array checked at this target.
            seen: list[tuple[str, list[str], list[int]]] = []
            present = [n for n in one_of if f"{t}/{n}" in view.group_attrs]
            # A member left out of the view (its metadata failed) is neither
            # present nor absent: its own error stands alone.
            unknown = any(f"{t}/{n}" in view.unknown for n in one_of)
            if one_of and not unknown and len(present) != 1:
                result.errors.append(
                    f"[{level_name}] '{t}' must hold exactly one of {_j(one_of)}, "
                    f"found {_j(present)}"
                )
            for gname, group in placed.items():
                if gname in one_of and gname not in present:
                    continue
                _check_group_arrays(view, children, t, gname, group, level_name, result, seen)
            _check_shared_dims(t, seen, level_name, result)


def _check_group_arrays(
    view: StoreView,
    children: dict[str, list[str]],
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
    for array_name, aspec in sorted(group.get("arrays", {}).items()):
        if "prefix" not in aspec:
            _check_array(view, f"{prefix}/{array_name}", aspec, level_name, result, seen)
            continue
        start = f"{prefix}/{aspec['prefix']}"
        members = [r for r in children.get(prefix, []) if r.startswith(start)]
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
    ``fill_value`` (when the spec declares one), ``dimension_names`` against the spec's ``dims``, the ``units`` attr and the
    array's own ``attrs``."""
    if rel in view.group_attrs:
        result.errors.append(f"[{level_name}] '{rel}' is a group, expected an array")
        return
    arr = view.arrays.get(rel)
    if arr is None:
        if aspec.get("required", False) and not _unknown(view, rel):
            result.errors.append(f"[{level_name}] required array '{rel}' is missing")
        return
    allowed = aspec["dtype"] if isinstance(aspec["dtype"], list) else [aspec["dtype"]]
    if arr.dtype not in allowed:
        result.errors.append(
            f"[{level_name}] array '{rel}' dtype {_j(arr.dtype)} != expected {_j(aspec['dtype'])}"
        )
    if "fill_value" in aspec:
        want, got = aspec["fill_value"], arr.fill_value
        # Exact: an integer, never a float (Zarr v3 writes an integer fill as one).
        if isinstance(got, bool) or not isinstance(got, int) or got != want:
            result.errors.append(
                f"[{level_name}] array '{rel}' fill_value {_j(got)} != expected {_j(want)}"
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
    dims = _j(aspec["dims"])
    if arr.dimension_names is None:
        result.errors.append(
            f"[{level_name}] array '{rel}' has no dimension_names (expected {dims})"
        )
    elif arr.dimension_names != aspec["dims"]:
        result.errors.append(
            f"[{level_name}] array '{rel}' dimension_names "
            f"{_j(arr.dimension_names)} != expected {dims}"
        )
    if "units" in aspec:
        units = _j(aspec["units"])
        if "units" not in arr.attrs:
            result.errors.append(
                f"[{level_name}] array '{rel}' has no units attr (expected {units})"
            )
        elif arr.attrs["units"] != aspec["units"]:
            result.errors.append(
                f"[{level_name}] array '{rel}' units {_j(arr.attrs['units'])} != expected {units}"
            )
    elif "units" in arr.attrs:
        result.errors.append(
            f"[{level_name}] array '{rel}' has a units attr {_j(arr.attrs['units'])} "
            "but declares no unit"
        )
    _check_attrs(arr.attrs, aspec.get("attrs", {}), rel, level_name, result)


def _unknown(view: StoreView, rel: str) -> bool:
    """True when the node at ``rel``, or a group above it, was left out of the
    view: its metadata did not parse or could not be read or listed."""
    parts = rel.split("/")
    return any("/".join(parts[:i]) in view.unknown for i in range(1, len(parts) + 1))


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
    for name, aspec in sorted(declared.items()):
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
                f"{_j(expected)}, got {_j(attrs[name])}"
            )
        elif "enum" in aspec and attrs[name] not in aspec["enum"]:
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must be one of "
                f"{_j(aspec['enum'])}, got {_j(attrs[name])}"
            )
        elif "minimum" in aspec and attrs[name] < aspec["minimum"]:
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must be "
                f">= {_j(aspec['minimum'])}, got {_j(attrs[name])}"
            )
        elif "maximum" in aspec and attrs[name] > aspec["maximum"]:
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must be "
                f"<= {_j(aspec['maximum'])}, got {_j(attrs[name])}"
            )
        elif "equals" in aspec and attrs[name] != aspec["equals"]:
            # JSON rendering keeps the message identical to the Rust binding's.
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must equal "
                f"{_j(aspec['equals'])}, got {_j(attrs[name])}"
            )
        elif aspec.get("format") == "uri-reference" and not _URI_REFERENCE_RE.fullmatch(
            attrs[name]
        ):
            result.errors.append(
                f"[{level_name}] attribute '{name}' at '{where}' must be a URI reference "
                f"(RFC 3986), not an absolute path and not a file: URI, got "
                f"{_j(attrs[name])}"
            )


def _negotiate_versions(
    view: StoreView, spec: dict, levels: set[str], result: ValidationResult
) -> None:
    """Presence/encoding check + two-axis range negotiation for on-disk versions.

    Only versions whose ``level`` is in ``levels`` (the detected level(s)) are
    negotiated, so a single-level store is not penalised for lacking the other
    level's version attr.
    """
    for attr_name, vspec in sorted(spec.get("versions", {}).items()):
        if vspec.get("level") not in levels:
            continue
        if attr_name not in view.root_attrs:
            result.errors.append(f"root attribute '{attr_name}' is required but missing")
            continue
        value = view.root_attrs[attr_name]
        on_disk = vspec["on_disk"]
        if on_disk == "integer" and not _is_int(value):
            result.errors.append(
                f"root attribute '{attr_name}' must be an integer on disk, got {_j(value)}"
            )
            continue
        if on_disk == "string" and not isinstance(value, str):
            result.errors.append(
                f"root attribute '{attr_name}' must be a string on disk, got {_j(value)}"
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
        return ([f"root attribute '{attr_name}': cannot parse version {_j(value)}"], [])
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
