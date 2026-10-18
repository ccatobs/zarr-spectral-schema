"""Emit synthetic Zarr v3 reference stores directly from the spec.

This is the seed of the inspector CLI's `emit-reference` (Slice 7, #8) and the
"binding parity" leg of the conformance stool: because the store is generated
*from* the spec, it cannot drift from it. Both bindings then validate the same
emitted artifact.

Slice 4 (#5) makes the generator placement-aware: it materialises the real
on-disk hierarchy the writers produce — `scan_<NNNNNN>` groups, the L0
`source`/`calibration` sub-groups, and (multi-frontend) `band_<M>` sub-groups —
placing every declared array and attr at its spec-declared placement. Nothing is
hand-coded per field; the shapes are derived entirely from the spec.

Usage:
    python conformance/generate.py <out_dir>
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import yaml
import zarr

SPEC = Path(__file__).resolve().parent.parent / "spec" / "schema.yaml"

# Declarative dtype names map straight onto zarr v3 data types; `string` and the
# integer/float names are all accepted by create_array(dtype=...) and round-trip
# to the same on-disk `data_type` the writers emit (probed against zarr 3.2).

# The scan group the fixtures wrap their content in (writer pads to 6 digits).
_SCAN = "scan_000001"

# The L0 identity/provenance attrs introduced in #13. A pre-#6 store is modelled
# by omitting exactly these; they are required since L0 3, so it is rejected.
_IDENTITY_L0_ATTRS = {"frontend", "pixels_per_array", "raw_attrs"}

# The attrs of the required L1 frame set of #19, from the spec list FRAME_ATTRS.
FRAME_ATTRS = tuple(
    yaml.safe_load(SPEC.read_text(encoding="utf-8"))["lists"]["FRAME_ATTRS"]
)
# Per-band identity of a multi-frontend scan (#34): scan-or-band placement.
BAND_IDENTITY = ("backend", "obs_id", "spectel1")
# Malformed `shape` values of one array: one fixture each.
BAD_SHAPES = {
    "string": ["1"],
    "null": [None],
    "nested": [[1]],
    "float": [1.0],
    "negative": [-1],
}

# The arrays of the required L1 frame set of #19 (scan-or-band, inline).
FRAME_ARRAYS = (
    "v_frame",
    "ref_lon",
    "ref_lat",
    "platform_lon",
    "platform_lat",
    "platform_alt",
)


def load_spec() -> dict:
    return yaml.safe_load(SPEC.read_text(encoding="utf-8"))


def _level_for(spec: dict, detect_attr: str) -> dict:
    """Return the level whose ``detect`` attr equals ``detect_attr``."""
    for level in spec["levels"].values():
        if level.get("detect") == detect_attr:
            return level
    raise KeyError(f"no level detected by {detect_attr!r}")


def _sample_attr(aspec: dict, name: str):
    """A representative on-disk value for a declared attr of the given type."""
    if "equals" in aspec:
        return aspec["equals"]
    if "enum" in aspec:
        return aspec["enum"][0]
    t = aspec["type"]
    if t == "string":
        return f"{name}_value"
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    raise ValueError(f"unhandled attr type {t!r} for {name!r}")


def _write_attrs(
    group,
    declared: dict,
    omit_attrs: set[str],
    emit_optional: bool,
    overrides: dict | None = None,
    not_applicable: bool = False,
) -> None:
    """Write declared attrs onto ``group``.

    Required attrs are always emitted (a structurally-valid store needs them);
    optional attrs are emitted only when ``emit_optional`` (so the fixtures carry
    a realistic subset). ``omit_attrs`` drops specific attrs for negative stores.
    ``overrides`` substitutes a literal on-disk value for a named attr (forcing it
    to be written even when optional) — used to emit wrong-typed negatives whose
    value deliberately violates the declared ``type``. ``not_applicable`` writes
    the not-applicable sentinel ("NONE" for a string, null for a number) for
    every attr that admits one (``sentinel: true``).
    """
    overrides = overrides or {}
    for name, aspec in declared.items():
        if name in omit_attrs:
            continue
        if name in overrides:
            group.attrs[name] = overrides[name]
            continue
        if not aspec.get("required", False) and not emit_optional:
            continue
        if not_applicable and aspec.get("sentinel"):
            group.attrs[name] = "NONE" if aspec["type"] == "string" else None
            continue
        group.attrs[name] = _sample_attr(aspec, name)


def _write_arrays(
    parent,
    group_spec: dict,
    rel_prefix: str,
    omit: set[str],
    emit_optional: bool,
    array_overrides: dict[str, dict] | None = None,
    sharded: bool = False,
) -> None:
    """Create a group's arrays under ``parent``.

    ``rel_prefix`` is the store-relative path of ``parent`` (for ``omit`` keys).
    Required arrays are always created; optional arrays only when ``emit_optional``
    (the fixtures emit a realistic subset so optional-present paths are exercised).
    An array family (``prefix``) is emitted as its first member ``<prefix>0``.
    Every array is written with the spec's ``dims`` as its zarr v3
    ``dimension_names`` and, where the spec declares ``units``, a ``units`` attr.
    ``array_overrides`` maps a store-relative path to ``dtype``/``ndim``/
    ``dims``/``units``/``shape``/``fill_value`` values that replace the declared ones
    (negatives; ``None`` writes no ``dimension_names`` / no ``units`` attr), and
    ``attr_values`` to literal array attr values. ``sharded`` writes every axis
    with length 2 and a chunk layout other than the writer default: 5-D arrays
    (C, D, R, A, S) get the recommended one shard per pixel and subscan
    (C, D, 1, 1, 1) with inner chunks (C/2, D, 1, 1, 1); the other arrays one
    chunk per element.
    """
    array_overrides = array_overrides or {}
    for array_name, aspec in group_spec.get("arrays", {}).items():
        name = f"{aspec['prefix']}0" if "prefix" in aspec else array_name
        rel = f"{rel_prefix}/{name}"
        if rel in omit:
            continue
        if not aspec.get("required", False) and not emit_optional:
            continue
        aspec = {**aspec, **array_overrides.get(rel, {})}
        ndim = aspec["ndim"]
        layout = {"shape": (1,) * ndim}
        if sharded:
            layout = {"shape": (2,) * ndim, "chunks": (1,) * ndim}
            if ndim == 5:
                layout.update(shards=(2, 2, 1, 1, 1), chunks=(1, 2, 1, 1, 1))
        if "shape" in aspec:
            layout["shape"] = aspec["shape"]
        # A dtype set (#20): write its first name, as the producers do by default.
        dtype = (
            aspec["dtype"][0] if isinstance(aspec["dtype"], list) else aspec["dtype"]
        )
        arr = parent.create_array(
            name=name,
            dtype=dtype,
            dimension_names=aspec.get("dims"),
            attributes={} if aspec.get("units") is None else {"units": aspec["units"]},
            # NaN fill on float arrays: the not-applicable value of an array.
            # A declared fill_value (L0 counts: saturated counter) wins.
            fill_value=aspec.get(
                "fill_value", float("nan") if dtype.startswith("float") else None
            ),
            **layout,
        )
        _write_attrs(
            arr, aspec.get("attrs", {}), set(), emit_optional, aspec.get("attr_values")
        )


def _write_version_attrs(
    root, spec: dict, level: str, overrides: dict[str, str | int]
) -> None:
    """Write the root version attrs for ``level``, encoded per their ``on_disk``.

    Only the version(s) whose ``level`` matches are written, so an L1 store does
    not accidentally carry ``l0_schema_version`` (which would flip detection).
    """
    for attr_name, vspec in spec.get("versions", {}).items():
        if vspec["level"] != level:
            continue
        raw = overrides.get(attr_name, vspec["value"])
        root.attrs[attr_name] = int(raw) if vspec["on_disk"] == "integer" else str(raw)


def _patch_meta(store: Path, rel: str, patch) -> None:
    """Rewrite the zarr.json of node ``rel`` through ``patch(meta)``."""
    path = store / rel / "zarr.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    patch(meta)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# ── store emitters (one per shape) ─────────────────────────────────────────


def emit_l0(
    out_dir: Path,
    *,
    scans: dict[str, list[str]] | None = None,
    n_bands: int = 0,
    omit: set[str] | None = None,
    omit_attrs: set[str] | None = None,
    versions: dict[str, str | int] | None = None,
    scan_attr_overrides: dict | None = None,
    array_overrides: dict[str, dict] | None = None,
    sharded: bool = False,
    not_applicable: bool = False,
) -> Path:
    """Emit an L0 store: scan_<N>/<group>/<arrays> + attrs.

    ``scans`` maps each scan group name to the ``one_of`` groups it holds. The
    default is one scan per ``one_of`` member (scan_000001 → source,
    scan_000002 → calibration), the shape zarr-fits writes. Groups outside
    ``one_of`` go into every scan. ``n_bands`` > 0 emits a multi-frontend store:
    scan_<N>/band_<B>/<group>/<arrays>, the scan-or-band attrs on each band.
    ``versions`` overrides the on-disk ``l0_schema_version`` (integer axis) for
    the version-negotiation fixtures while the structure stays spec-current.
    ``scan_attr_overrides`` substitutes literal scan-group attr values (for the
    wrong-typed identity-contract negatives, e.g. ``raw_attrs`` as an integer).
    """
    omit = omit or set()
    omit_attrs = omit_attrs or set()
    spec = load_spec()
    level = _level_for(spec, "l0_schema_version")
    one_of = level.get("one_of", [])
    if scans is None:
        scans = {f"scan_{i:06d}": [g] for i, g in enumerate(one_of, start=1)} or {
            _SCAN: []
        }

    root = zarr.open_group(store=str(out_dir), mode="w", zarr_format=3)
    _write_version_attrs(root, spec, "L0", versions or {})
    _write_attrs(
        root,
        level.get("root_attrs", {}),
        omit_attrs,
        emit_optional=True,
        not_applicable=not_applicable,
    )

    for scan_name, chosen in scans.items():
        scan = root.require_group(scan_name)
        _write_attrs(
            scan,
            level.get("scan_attrs", {}),
            omit_attrs,
            emit_optional=True,
            overrides=scan_attr_overrides,
        )
        targets = [(scan, scan_name)]
        if n_bands:
            targets = []
            for idx in range(n_bands):
                band = scan.require_group(f"band_{idx}")
                _write_attrs(band, level.get("band_attrs", {}), omit_attrs, True)
                targets.append((band, f"{scan_name}/band_{idx}"))
        for target, rel in targets:
            _write_attrs(
                target,
                level.get("scan_or_band_attrs", {}),
                omit_attrs,
                emit_optional=True,
                overrides=scan_attr_overrides,
            )
            # L0 groups are `placement: scan_or_band, inline: false` sub-groups.
            for gname, gspec in level.get("groups", {}).items():
                if gname in one_of and gname not in chosen:
                    continue
                sub = target.require_group(gname)
                _write_arrays(
                    sub, gspec, f"{rel}/{gname}", omit, True, array_overrides, sharded
                )
    return out_dir


def emit_l1_single(
    out_dir: Path,
    *,
    omit: set[str] | None = None,
    omit_attrs: set[str] | None = None,
    versions: dict[str, str | int] | None = None,
    scan_attr_overrides: dict | None = None,
    array_overrides: dict[str, dict] | None = None,
    sharded: bool = False,
    not_applicable: bool = False,
) -> Path:
    """Emit an L1 single-frontend store: scan_000001/<arrays> directly.

    ``versions`` overrides the on-disk ``cal_schema_version`` for the
    version-negotiation fixtures while the structure stays spec-current.
    ``scan_attr_overrides`` substitutes literal values for scan-group attrs
    (``scan_attrs`` and ``scan_or_band_attrs`` both land on the scan group).
    ``not_applicable`` writes the sentinel in every field that admits one.
    """
    omit = omit or set()
    omit_attrs = omit_attrs or set()
    spec = load_spec()
    level = _level_for(spec, "cal_schema_version")

    root = zarr.open_group(store=str(out_dir), mode="w", zarr_format=3)
    _write_version_attrs(root, spec, "L1", versions or {})
    _write_attrs(root, level.get("root_attrs", {}), omit_attrs, emit_optional=True)

    scan = root.require_group(_SCAN)
    # No band_<M> children ⇒ single-frontend: scan_attrs + scan_or_band_attrs
    # both land on the scan group.
    for section in ("scan_attrs", "scan_or_band_attrs"):
        _write_attrs(
            scan,
            level.get(section, {}),
            omit_attrs,
            emit_optional=True,
            overrides=scan_attr_overrides,
            not_applicable=not_applicable,
        )

    for gname, gspec in level.get("groups", {}).items():
        if gspec.get("placement") != "scan_or_band":
            continue
        if gspec.get("inline", False):
            _write_arrays(scan, gspec, _SCAN, omit, True, array_overrides, sharded)
        else:
            sub = scan.require_group(gname)
            _write_arrays(
                sub, gspec, f"{_SCAN}/{gname}", omit, True, array_overrides, sharded
            )
    return out_dir


def emit_l1_multi(
    out_dir: Path,
    *,
    n_bands: int = 2,
    omit: set[str] | None = None,
    omit_attrs: set[str] | None = None,
    omit_band_attrs: dict[int, set[str]] | None = None,
) -> Path:
    """Emit an L1 multi-frontend store: scan_000001/band_<M>/<arrays>.

    Per the placement model, ``scan_or_band`` attrs/arrays land on EACH band;
    ``line``/``rest_freq_hz`` are therefore band-level here (not on the scan
    group), proving the rule is data-driven. ``omit_band_attrs`` drops attrs on a
    specific band (for the missing-band_index negative).
    """
    omit = omit or set()
    omit_attrs = omit_attrs or set()
    omit_band_attrs = omit_band_attrs or {}
    spec = load_spec()
    level = _level_for(spec, "cal_schema_version")

    root = zarr.open_group(store=str(out_dir), mode="w", zarr_format=3)
    _write_version_attrs(root, spec, "L1", {})
    _write_attrs(root, level.get("root_attrs", {}), omit_attrs, emit_optional=True)

    scan = root.require_group(_SCAN)
    # Multi-frontend: scan_attrs on the scan group, but NOT scan_or_band_attrs
    # (those move to each band).
    _write_attrs(scan, level.get("scan_attrs", {}), omit_attrs, emit_optional=True)

    sob_groups = {
        name: g
        for name, g in level.get("groups", {}).items()
        if g.get("placement") == "scan_or_band"
    }

    for idx in range(n_bands):
        band = scan.require_group(f"band_{idx}")
        band_rel = f"{_SCAN}/band_{idx}"
        drop = omit_attrs | omit_band_attrs.get(idx, set())
        _write_attrs(band, level.get("band_attrs", {}), drop, emit_optional=True)
        _write_attrs(
            band, level.get("scan_or_band_attrs", {}), drop, emit_optional=True
        )
        for gname, gspec in sob_groups.items():
            if gspec.get("inline", False):
                _write_arrays(band, gspec, band_rel, omit, emit_optional=True)
            else:
                sub = band.require_group(gname)
                _write_arrays(
                    sub, gspec, f"{band_rel}/{gname}", omit, emit_optional=True
                )
    return out_dir


# ── back-compat shim ─────────────────────────────────────────────────────
#
# The version-negotiation fixtures address stores by `emit(...)`; keep that entry
# point but make it produce a structurally-valid L1 single-frontend store so the
# only thing those tests exercise is the version range check (not structure).


def emit(
    out_dir: Path,
    omit: set[str] | None = None,
    versions: dict[str, str | int] | None = None,
) -> Path:
    """Emit a structurally-valid L1 single-frontend store (version fixtures' base).

    ``omit`` and ``versions`` are forwarded to :func:`emit_l1_single`. The array
    ``omit`` keys are store-relative paths under the scan group (e.g.
    ``scan_000001/spectra``).
    """
    return emit_l1_single(out_dir, omit=omit, versions=versions)


# Version-negotiation fixtures (Slice 3, #4). Each overrides one on-disk version
# relative to the spec's current `value` (cal=2.2); structure stays spec-current
# so the only thing under test is the version range check. Expected outcome is
# named for the binding tests that validate these same stores.
# min_supported is the oldest version whose stores pass the structural check,
# which today equals `value`, so an older minor is below the floor.
VERSION_FIXTURES: dict[str, dict[str, str | int]] = {
    "ver_newer_minor.zarr": {
        "cal_schema_version": "2.9"
    },  # newer minor → warn once, pass
    "ver_newer_major.zarr": {"cal_schema_version": "3.0"},  # newer major → error
    "ver_below_floor.zarr": {
        "cal_schema_version": "1.3"
    },  # below min_supported → one error
}

# The integer-axis (L0) negotiation fixture is a structurally-valid L0 store with
# its `l0_schema_version` overridden; it lives in its own dict because it is
# emitted by `emit_l0`, not the L1 `emit` shim above.
L0_VERSION_FIXTURES: dict[str, dict[str, str | int]] = {
    "ver_l0_newer_major.zarr": {
        "l0_schema_version": 4
    },  # integer axis, newer major → error
    "ver_l0_below_floor.zarr": {
        "l0_schema_version": 2
    },  # below min_supported → one error
}


def emit_real_output_fixtures(out: Path) -> None:
    """Fixtures for #18 (accept real producer output, reject broken output).

    Both bindings validate these same stores: the Python tests call this
    function, the Rust tests run ``main()``.
    """
    one_of = _level_for(load_spec(), "l0_schema_version")["one_of"]
    # A scan holds exactly one of the one_of groups: both, or neither, is an error.
    emit_l0(out / "l0_both_groups.zarr", scans={_SCAN: list(one_of)})
    emit_l0(out / "l0_no_group.zarr", scans={_SCAN: []})
    # A scan number may take 7+ digits: the validator checks that scan (here: its
    # missing data_5d is reported).
    emit_l0(
        out / "l0_wide_scan.zarr",
        scans={_SCAN: [one_of[0]], "scan_1003537": [one_of[0]]},
        omit={f"scan_1003537/{one_of[0]}/data_5d"},
    )
    # A root child named scan_* that does not match the scan pattern is an error.
    emit_l0(
        out / "l0_bad_scan_name.zarr",
        scans={_SCAN: [one_of[0]], "scan_12": [one_of[0]]},
    )
    # The L0 completeness marker is type-checked (boolean) and holds true.
    emit_l0(out / "l0_bad_l0_complete.zarr", scan_attr_overrides={"l0_complete": "yes"})
    emit_l0(out / "l0_incomplete.zarr", scan_attr_overrides={"l0_complete": False})
    # calibrate writes the provenance attrs as JSON objects.
    emit_l1_single(
        out / "l1_provenance_objects.zarr",
        scan_attr_overrides={
            "source_l0_scans": {"source_scan": 1, "cal_scan": 2},
            "processing_steps": {"skydiff": False, "obs_mode": "TotalPower"},
        },
    )
    # The completeness marker holds `true` (a DSL value constraint).
    emit_l1_single(
        out / "l1_incomplete.zarr", scan_attr_overrides={"l1_complete": False}
    )
    # The skydiff group declares the spectrum_ family: spectrum_0 is checked.
    emit_l1_single(
        out / "l1_bad_skydiff.zarr",
        array_overrides={
            f"{_SCAN}/skydiff/spectrum_0": {
                "dtype": "float32",
                "ndim": 2,
                "dims": ["channel", "receiver"],
            }
        },
    )
    # L1 declares raw_attrs (verbatim source-keyword provenance) as an object.
    emit_l1_single(out / "l1_bad_raw_attrs.zarr", scan_attr_overrides={"raw_attrs": 42})

    # One parse path in both bindings: one fixture per former Rust/Python divergence.
    # A non-finite attr value (zarr-python writes a bare NaN token).
    emit_l1_single(
        out / "l1_nan_attr.zarr", scan_attr_overrides={"rest_freq_hz": float("nan")}
    )
    # A zarr.json that does not parse.
    emit_l1_single(out / "l1_malformed_meta.zarr")
    (out / "l1_malformed_meta.zarr" / _SCAN / "t_int" / "zarr.json").write_text(
        "{not json"
    )
    # Metadata nested past any parser depth limit: 100,000 unclosed `[` in an attr.
    emit_l1_single(out / "l1_deep_meta.zarr")
    (out / "l1_deep_meta.zarr" / _SCAN / "zarr.json").write_text(
        '{"zarr_format": 3, "node_type": "group", "attributes": {"deep": '
        + "[" * 100_000
    )
    # The object form of data_type (zarr v3 extension form).
    emit_l1_single(out / "l1_object_dtype.zarr")
    _patch_meta(
        out / "l1_object_dtype.zarr",
        f"{_SCAN}/t_int",
        lambda m: m.__setitem__(
            "data_type", {"name": m["data_type"], "configuration": {}}
        ),
    )
    # A group where the spec names an (optional) array.
    store = emit_l1_single(out / "l1_group_at_array.zarr")
    shutil.rmtree(store / _SCAN / "lloadsn")
    zarr.open_group(store=str(store), mode="r+")[_SCAN].require_group("lloadsn")
    # Unicode digits in a version and in a scan name (ASCII-only grammar).
    emit_l1_single(
        out / "ver_unicode_digits.zarr", versions={"cal_schema_version": "١.٣"}
    )
    emit_l0(
        out / "l0_unicode_scan_name.zarr",
        scans={_SCAN: [one_of[0]], "scan_٠٠٠٠٠٢": [one_of[0]]},
    )


# source_l0_store values (#20) → (on-disk value, conformant?). Both bindings check
# the same verdicts (test_storage_contract.py, storage_contract.rs).
SOURCE_L0_STORE_FIXTURES: dict[str, tuple[str, bool]] = {
    "l1_l0_store_relative.zarr": ("../../zarr/SOFIA_UPGREAT_ORION_KL.zarr", True),
    "l1_l0_store_s3.zarr": ("s3://ccat-archive/zarr/scan%20set.zarr", True),
    "l1_l0_store_abs_path.zarr": ("/data/zarr/SOFIA_UPGREAT_ORION_KL.zarr", False),
    "l1_l0_store_empty.zarr": ("", False),
    "l1_l0_store_space.zarr": ("../zarr/my store.zarr", False),
    "l1_l0_store_backslash.zarr": ("..\\zarr\\x.zarr", False),
    "l1_l0_store_bad_escape.zarr": ("../zarr/x%zz.zarr", False),
    "l1_l0_store_non_ascii.zarr": ("../zarr/m\u00fcnchen.zarr", False),
    # A file: URI names a file on one host, as an absolute path does (#33).
    "l1_l0_store_file_uri.zarr": ("file:///data/zarr/x.zarr", False),
}


def emit_storage_fixtures(out: Path) -> None:
    """Fixtures for #20 (object storage and production volume).

    Both bindings validate these same stores: the Python tests call this
    function, the Rust tests run ``main()``.
    """
    # Chunk independence: a store with a chunk layout other than the writer
    # default (the recommended one shard per pixel and subscan) is conformant.
    emit_l0(out / "l0_sharded.zarr", sharded=True)
    emit_l1_single(out / "l1_sharded.zarr", sharded=True)
    # L1 spectra and t_sys accept float32 as well as float64.
    for dtype in ("float32", "float64"):
        emit_l1_single(
            out / f"l1_{dtype}.zarr",
            array_overrides={
                f"{_SCAN}/spectra": {"dtype": dtype},
                f"{_SCAN}/t_sys": {"dtype": dtype},
            },
        )
    # An integer dtype is outside the set.
    emit_l1_single(
        out / "l1_int16_spectra.zarr",
        array_overrides={f"{_SCAN}/spectra": {"dtype": "int16"}},
    )
    # keepbits (the bit rounding a writer applied) is an integer where present.
    emit_l1_single(
        out / "l1_keepbits.zarr",
        array_overrides={f"{_SCAN}/spectra": {"attr_values": {"keepbits": 12}}},
    )
    emit_l1_single(
        out / "l1_bad_keepbits.zarr",
        array_overrides={f"{_SCAN}/spectra": {"attr_values": {"keepbits": "12"}}},
    )
    # L0 counts are uint32 with the saturated-counter fill value (#27).
    counts = f"{_SCAN}/source/data_5d"
    # The pre-#27 store: int32 counts with fill value i32::MIN, as the old stores carry.
    emit_l0(
        out / "l0_int32_counts.zarr",
        array_overrides={counts: {"dtype": "int32", "fill_value": -(2**31)}},
    )
    # A JSON float where the spec declares an integer is a mismatch in both bindings.
    store = emit_l0(out / "l0_float_fill_value.zarr")
    _patch_meta(store, counts, lambda m: m.__setitem__("fill_value", 4294967295.0))
    emit_l0(out / "l0_bad_fill_value.zarr", array_overrides={counts: {"fill_value": 0}})

    # One FITS header per pixel: raw_fits_headers and frontend_backends are
    # [receiver, array, subscan]. The [subscan] shape of the older stores and an
    # [array, subscan] shape are errors, and so is a receiver or array length
    # that differs from data_5d.
    def _header_override(**over):
        return {
            f"{_SCAN}/source/{n}": over
            for n in ("raw_fits_headers", "frontend_backends")
        }

    emit_l0(
        out / "l0_headers_1d.zarr",
        array_overrides=_header_override(ndim=1, dims=["subscan"]),
    )
    emit_l0(
        out / "l0_headers_array_subscan.zarr",
        array_overrides=_header_override(ndim=2, dims=["array", "subscan"]),
    )
    # The calibration group holds its own copies of the per-pixel headers.
    emit_l0(
        out / "l0_cal_headers_1d.zarr",
        array_overrides={
            f"scan_000002/calibration/{n}": {"ndim": 1, "dims": ["subscan"]}
            for n in ("raw_fits_headers", "frontend_backends")
        },
    )
    emit_l0(
        out / "l0_headers_receiver_mismatch.zarr",
        array_overrides=_header_override(shape=(2, 1, 1)),
    )
    emit_l0(
        out / "l0_headers_array_mismatch.zarr",
        array_overrides=_header_override(shape=(1, 2, 1)),
    )
    # gamma is counts per second per kelvin; the old unit was ct/K.
    emit_l1_single(
        out / "l1_gamma_old_unit.zarr",
        array_overrides={f"{_SCAN}/gamma": {"units": "ct/K"}},
    )
    # The valid dump count per subscan is int32 [S] where present.
    bad = {"dtype": "float64", "ndim": 2, "dims": ["dump", "subscan"]}
    emit_l0(
        out / "l0_bad_valid_dumps.zarr",
        array_overrides={f"{_SCAN}/source/n_valid_dumps": bad},
    )
    emit_l1_single(
        out / "l1_bad_valid_dumps.zarr",
        array_overrides={f"{_SCAN}/n_valid_dumps": bad},
    )
    # The local walk does not follow a symlinked directory: scan_000002 links
    # to a scan without spectra, and the store stays green.
    emit_l1_single(out / "l1_symlink_target.zarr", omit={f"{_SCAN}/spectra"})
    store = emit_l1_single(out / "l1_symlinked_scan.zarr")
    (store / "scan_000002").symlink_to(
        Path("..") / "l1_symlink_target.zarr" / _SCAN, target_is_directory=True
    )
    # A local path is a path: `::` and `name:` in it are not URL syntax.
    emit_l1_single(out / "colon_dir" / "miss_array.zarr", omit={f"{_SCAN}/spectra"})
    (out / "colon_dir").rename(out / "a::b")
    # source_l0_store holds an RFC 3986 URI reference, never an absolute path.
    for name, (value, _ok) in SOURCE_L0_STORE_FIXTURES.items():
        emit_l1_single(out / name, scan_attr_overrides={"source_l0_store": value})


def emit_self_description_fixtures(out: Path) -> None:
    """Fixtures for #19 (dimensions, units, flag bits and the scientific frame).

    Both bindings validate these same stores: the Python tests call this
    function, the Rust tests run ``main()``.
    """
    spectra = f"{_SCAN}/spectra"
    # 1. Each array carries its spec dims as zarr v3 dimension_names.
    emit_l1_single(
        out / "l1_no_dimension_names.zarr", array_overrides={spectra: {"dims": None}}
    )
    emit_l1_single(
        out / "l1_wrong_dimension_names.zarr",
        array_overrides={
            spectra: {"dims": ["subscan", "array", "receiver", "dump", "channel"]}
        },
    )
    # ...and the units attr the spec declares for it.
    emit_l1_single(out / "l1_no_units.zarr", array_overrides={spectra: {"units": None}})
    emit_l1_single(
        out / "l1_wrong_units.zarr", array_overrides={spectra: {"units": "mK"}}
    )
    # Arrays at one target agree on the length of each shared dimension.
    emit_l1_single(
        out / "l1_channel_mismatch.zarr",
        array_overrides={f"{_SCAN}/tau_signal": {"shape": (2,)}},
    )
    emit_l1_single(
        out / "l1_pixel_offset_subscans.zarr",
        array_overrides={f"{_SCAN}/pixel_offset_lon": {"shape": (1, 1, 3)}},
    )
    # The valid dump count (#20) shares the subscan axis S (deferred to #19).
    emit_l0(
        out / "l0_valid_dumps_subscans.zarr",
        array_overrides={f"{_SCAN}/source/n_valid_dumps": {"shape": (3,)}},
    )
    emit_l1_single(
        out / "l1_valid_dumps_subscans.zarr",
        array_overrides={f"{_SCAN}/n_valid_dumps": {"shape": (3,)}},
    )
    # 3. A scan without the frame set, an L0 store without its site location.
    emit_l1_single(
        out / "l1_no_frame.zarr",
        omit_attrs=set(FRAME_ATTRS),
        omit={f"{_SCAN}/{name}" for name in FRAME_ARRAYS},
    )
    emit_l0(
        out / "l0_no_site.zarr",
        omit_attrs={"site_longitude_deg", "site_latitude_deg", "site_altitude_m"},
    )
    # Where a field does not apply, it holds the not-applicable sentinel; a
    # field that always applies admits none.
    emit_l1_single(out / "l1_not_applicable.zarr", not_applicable=True)
    emit_l0(out / "l0_not_applicable.zarr", not_applicable=True)
    emit_l1_single(out / "l1_eta_fss_null.zarr", scan_attr_overrides={"eta_fss": None})
    # A vocabulary field holds a value of its declared set.
    emit_l1_single(out / "l1_bad_specsys.zarr", scan_attr_overrides={"specsys": "LSR"})
    # 4. Multi-frontend L1: identity, quality record and provenance sit on each
    # band; a scan group that holds one of them is an error.
    emit_l1_multi(out / "l1_multi_band_without_qa.zarr", omit_band_attrs={1: {"qa"}})
    store = emit_l1_multi(out / "l1_multi_scan_arrays.zarr")
    zarr.open_group(store=str(store), mode="r+")[_SCAN].attrs["arrays"] = ["HFA"]
    # Required since L0 3 / L1 2.0 (maintainer, 2026-10-14): the L0 completeness
    # marker on each scan placement target, the L1 identity at scan-or-band.
    emit_l0(out / "l0_no_l0_complete.zarr", omit_attrs={"l0_complete"})
    emit_l1_single(out / "l1_no_identity.zarr", omit_attrs={"frontend", "raw_attrs"})
    # L0 3 (#34): `arrays` sits where `pixels_per_array` sits, so a reader can
    # pair the pixel lists with their arrays.
    emit_l0(out / "l0_no_arrays.zarr", omit_attrs={"arrays"})
    # L0 3 / L1 2.0 (#34): the per-band identity of a multi-frontend scan sits on
    # each band; one value on the scan group would be band 0's.
    for store in (
        emit_l0(out / "l0_multi_scan_identity.zarr", n_bands=2),
        emit_l1_multi(out / "l1_multi_scan_identity.zarr"),
    ):
        scan = zarr.open_group(store=str(store), mode="r+")[_SCAN]
        scan.attrs.update({n: f"{n}_value" for n in BAND_IDENTITY})
    # A shape that is not a list of non-negative integers is a finding of its
    # own, not an exception and not a length (review of PR 25, round 1).
    for name, shape in BAD_SHAPES.items():
        store = emit_l1_single(out / f"l1_shape_{name}.zarr")
        _patch_meta(
            store,
            f"{_SCAN}/t_int",
            lambda m, shape=shape: m.__setitem__("shape", shape),
        )
    # Multi-frontend L0: scan_N/band_B/, every band checked.
    emit_l0(out / "l0_multi.zarr", n_bands=2)
    emit_l0(
        out / "l0_multi_band_without_data.zarr",
        n_bands=2,
        omit={f"{_SCAN}/band_1/source/data_5d"},
    )


def emit_cleanup_fixtures(out: Path) -> None:
    """Fixtures for #33 (validator and spec findings of the contract epic).

    Both bindings validate these same stores: the Python tests call this
    function, the Rust tests run ``main()``.
    """
    # A store that carries both detect attrs, below both floors, with one
    # malformed zarr.json: the version errors come in one order, the parse
    # error once.
    store = emit_l1_single(
        out / "two_levels.zarr", versions={"cal_schema_version": "1.3"}
    )
    zarr.open_group(store=str(store), mode="r+").attrs["l0_schema_version"] = 2
    (store / _SCAN / "t_int" / "zarr.json").write_text("{not json")
    # An array where a scan group belongs.
    store = emit_l0(out / "l0_array_at_scan_path.zarr")
    zarr.open_group(store=str(store), mode="r+").create_array(
        "scan_000003", shape=(1,), dtype="float64"
    )
    # A multi-frontend scan group holds band groups only: an undeclared array
    # (L1) and a sub-group (L0) there are findings.
    store = emit_l1_multi(out / "l1_multi_scan_array.zarr")
    zarr.open_group(store=str(store), mode="r+")[_SCAN].create_array(
        "spectra", shape=(1,), dtype="float64"
    )
    store = emit_l0(out / "l0_multi_scan_group.zarr", n_bands=2)
    zarr.open_group(store=str(store), mode="r+")[_SCAN].require_group("source")
    # A leftover virtual scan group of the old layout: scan N + 1000000 x b
    # beside scan N (ADR 0011, the band layout).
    emit_l0(
        out / "l0_virtual_scan.zarr",
        scans={"scan_029951": ["source"], "scan_1029951": ["source"]},
    )
    # An axis name outside the dimension vocabulary.
    emit_l1_single(
        out / "l1_dims_outside_vocabulary.zarr",
        array_overrides={
            f"{_SCAN}/spectra": {
                "dims": ["channel", "time", "receiver", "array", "subscan"]
            }
        },
    )
    # A units attr on an array that declares no unit (flags is a bitmask).
    emit_l1_single(
        out / "l1_units_on_flags.zarr",
        array_overrides={f"{_SCAN}/flags": {"units": ""}},
    )
    # Non-ASCII text in a finding: no \\u escape in either binding.
    emit_l1_single(
        out / "l1_non_ascii_specsys.zarr", scan_attr_overrides={"specsys": "LSRü"}
    )
    # Units compare as strings, exactly (ADR 0012): a spaced spelling of the
    # gamma unit is a units error.
    emit_l1_single(
        out / "l1_gamma_spaced_unit.zarr",
        array_overrides={f"{_SCAN}/gamma": {"units": "ct / (K s)"}},
    )
    # keepbits counts kept mantissa bits: 0 to 52 (float64).
    for name, bits in (("l1_keepbits_above.zarr", 53), ("l1_keepbits_below.zarr", -1)):
        emit_l1_single(
            out / name,
            array_overrides={f"{_SCAN}/spectra": {"attr_values": {"keepbits": bits}}},
        )
    # Metadata nested 127 levels (serde_json's limit) parses in both bindings;
    # 128 levels is malformed in both (whole-epic review of PR #36).
    for depth in (127, 128):
        store = emit_l1_single(out / f"l1_depth_{depth}.zarr")
        # The root object and `attributes` are two levels; the rest are lists.
        nested = "[" * (depth - 2) + "]" * (depth - 2)
        (store / _SCAN / "t_int" / "zarr.json").write_text(
            (store / _SCAN / "t_int" / "zarr.json")
            .read_text(encoding="utf-8")
            .replace('"attributes": {', f'"attributes": {{"deep": {nested}, ', 1),
            encoding="utf-8",
        )
    # A one_of group whose zarr.json is cut short beside a valid one: the
    # parse error is the only finding (review of PR #40).
    store = emit_l0(
        out / "l0_truncated_one_of.zarr", scans={_SCAN: ["calibration", "source"]}
    )
    (store / _SCAN / "source" / "zarr.json").write_text('{"zarr_format": 3, "node_')
    # An exponent -0 (9.7e-0) is a number, not an integer -0 (review of PR #40).
    store = emit_l1_single(out / "l1_exponent_zero.zarr")
    meta = store / _SCAN / "zarr.json"
    text = meta.read_text(encoding="utf-8")
    meta.write_text(text.replace('"rest_freq_hz": 1.0', '"rest_freq_hz": 9.7e-0'))
    # JSON -0 in an integer attr: an integer in both bindings.
    store = emit_l1_single(out / "l1_negative_zero.zarr")
    meta = store / _SCAN / "zarr.json"
    text = meta.read_text(encoding="utf-8")
    meta.write_text(text.replace('"ref_channel_base": 0', '"ref_channel_base": -0'))


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python conformance/generate.py <out_dir>", file=sys.stderr)
        raise SystemExit(2)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)

    # The three structural shapes both bindings validate green.
    emit_l0(out / "l0.zarr")
    emit_l1_single(out / "l1_single.zarr")
    emit_l1_multi(out / "l1_multi.zarr")
    print(f"emitted: {out / 'l0.zarr'}")
    print(f"emitted: {out / 'l1_single.zarr'}")
    print(f"emitted: {out / 'l1_multi.zarr'}")

    # Structural negatives both bindings reject (naming the path/attr).
    emit_l1_single(out / "miss_array.zarr", omit={"scan_000001/spectra"})
    emit_l1_multi(out / "miss_band_index.zarr", omit_band_attrs={1: {"band_index"}})
    print(f"emitted: {out / 'miss_array.zarr'}")
    print(f"emitted: {out / 'miss_band_index.zarr'}")

    # Identity + provenance contract. The post-store (l0.zarr above) carries the
    # L0 identity attrs; a pre-store omits them and is rejected (required since
    # L0 3); wrong-typed values are rejected —
    # `raw_attrs` as a scalar (declared object) and the legacy dict form of
    # `pixels_per_array` (declared list-of-lists ⇒ JSON array), which the spec
    # deliberately does NOT admit. The raw_attrs bad value is `42` (no "object"
    # substring) so the negative proves a type-error rejection, not that the value
    # text happened to name the type.
    emit_l0(out / "l0_pre6.zarr", omit_attrs=_IDENTITY_L0_ATTRS)
    emit_l0(out / "l0_bad_raw_attrs.zarr", scan_attr_overrides={"raw_attrs": 42})
    emit_l0(
        out / "l0_bad_pixels_per_array.zarr",
        scan_attr_overrides={"pixels_per_array": {"HFA": ["P1", "P2"]}},
    )
    print(f"emitted: {out / 'l0_pre6.zarr'}")
    print(f"emitted: {out / 'l0_bad_raw_attrs.zarr'}")
    print(f"emitted: {out / 'l0_bad_pixels_per_array.zarr'}")

    emit_real_output_fixtures(out)
    emit_storage_fixtures(out)
    emit_self_description_fixtures(out)
    emit_cleanup_fixtures(out)

    # Version-negotiation fixtures: the cal_schema_version (string-axis) ones ride
    # an L1 store; the l0_schema_version (integer-axis) one rides an L0 store.
    for name, versions in VERSION_FIXTURES.items():
        emit(out / name, versions=versions)
        print(f"emitted: {out / name}")
    for name, versions in L0_VERSION_FIXTURES.items():
        emit_l0(out / name, versions=versions)
        print(f"emitted: {out / name}")


if __name__ == "__main__":
    main()
