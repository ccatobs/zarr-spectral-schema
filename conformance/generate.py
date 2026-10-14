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

# The additive L0 identity/provenance scan attrs introduced in #13. A pre-#6
# store is modelled by omitting exactly these (they are all `required: false`).
_IDENTITY_L0_ATTRS = {"frontend", "pixels_per_array", "raw_attrs"}


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
) -> None:
    """Write declared attrs onto ``group``.

    Required attrs are always emitted (a structurally-valid store needs them);
    optional attrs are emitted only when ``emit_optional`` (so the fixtures carry
    a realistic subset). ``omit_attrs`` drops specific attrs for negative stores.
    ``overrides`` substitutes a literal on-disk value for a named attr (forcing it
    to be written even when optional) — used to emit wrong-typed negatives whose
    value deliberately violates the declared ``type``.
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
        group.attrs[name] = _sample_attr(aspec, name)


def _write_arrays(
    parent,
    group_spec: dict,
    rel_prefix: str,
    omit: set[str],
    emit_optional: bool,
    array_overrides: dict[str, dict] | None = None,
) -> None:
    """Create a group's arrays under ``parent``.

    ``rel_prefix`` is the store-relative path of ``parent`` (for ``omit`` keys).
    Required arrays are always created; optional arrays only when ``emit_optional``
    (the fixtures emit a realistic subset so optional-present paths are exercised).
    An array family (``prefix``) is emitted as its first member ``<prefix>0``.
    ``array_overrides`` maps a store-relative path to ``dtype``/``ndim`` values
    that replace the declared ones (wrong-typed negatives).
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
        shape = tuple([1] * aspec["ndim"])
        parent.create_array(name=name, shape=shape, dtype=aspec["dtype"])


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
    omit: set[str] | None = None,
    omit_attrs: set[str] | None = None,
    versions: dict[str, str | int] | None = None,
    scan_attr_overrides: dict | None = None,
) -> Path:
    """Emit an L0 store: scan_<N>/<group>/<arrays> + attrs.

    ``scans`` maps each scan group name to the ``one_of`` groups it holds. The
    default is one scan per ``one_of`` member (scan_000001 → source,
    scan_000002 → calibration), the shape zarr-fits writes. Groups outside
    ``one_of`` go into every scan.
    ``versions`` overrides the on-disk ``l0_schema_version`` (integer axis) for
    the version-negotiation fixtures while the structure stays spec-current.
    ``scan_attr_overrides`` substitutes literal scan-group attr values (for the
    wrong-typed identity-contract negatives, e.g. ``raw_attrs`` as a string).
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
    _write_attrs(root, level.get("root_attrs", {}), omit_attrs, emit_optional=True)

    for scan_name, chosen in scans.items():
        scan = root.require_group(scan_name)
        _write_attrs(
            scan,
            level.get("scan_attrs", {}),
            omit_attrs,
            emit_optional=True,
            overrides=scan_attr_overrides,
        )
        # L0 groups are `placement: scan, inline: false` sub-groups.
        for gname, gspec in level.get("groups", {}).items():
            if gname in one_of and gname not in chosen:
                continue
            sub = scan.require_group(gname)
            _write_arrays(sub, gspec, f"{scan_name}/{gname}", omit, emit_optional=True)
    return out_dir


def emit_l1_single(
    out_dir: Path,
    *,
    omit: set[str] | None = None,
    omit_attrs: set[str] | None = None,
    versions: dict[str, str | int] | None = None,
    scan_attr_overrides: dict | None = None,
    array_overrides: dict[str, dict] | None = None,
) -> Path:
    """Emit an L1 single-frontend store: scan_000001/<arrays> directly.

    ``versions`` overrides the on-disk ``cal_schema_version`` for the
    version-negotiation fixtures while the structure stays spec-current.
    ``scan_attr_overrides`` substitutes literal values for scan-group attrs
    (``scan_attrs`` and ``scan_or_band_attrs`` both land on the scan group).
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
        )

    for gname, gspec in level.get("groups", {}).items():
        if gspec.get("placement") != "scan_or_band":
            continue
        if gspec.get("inline", False):
            _write_arrays(scan, gspec, _SCAN, omit, True, array_overrides)
        else:
            sub = scan.require_group(gname)
            _write_arrays(sub, gspec, f"{_SCAN}/{gname}", omit, True, array_overrides)
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
# relative to the spec's current `value` (cal=1.3); structure stays spec-current
# so the only thing under test is the version range check. Expected outcome is
# named for the binding tests that validate these same stores.
# min_supported is the oldest version whose stores pass the structural check,
# which today equals `value`, so an older minor is below the floor.
VERSION_FIXTURES: dict[str, dict[str, str | int]] = {
    "ver_newer_minor.zarr": {
        "cal_schema_version": "1.9"
    },  # newer minor → warn once, pass
    "ver_newer_major.zarr": {"cal_schema_version": "2.0"},  # newer major → error
    "ver_below_floor.zarr": {
        "cal_schema_version": "1.2"
    },  # below min_supported → one error
}

# The integer-axis (L0) negotiation fixture is a structurally-valid L0 store with
# its `l0_schema_version` overridden; it lives in its own dict because it is
# emitted by `emit_l0`, not the L1 `emit` shim above.
L0_VERSION_FIXTURES: dict[str, dict[str, str | int]] = {
    "ver_l0_newer_major.zarr": {
        "l0_schema_version": 3
    },  # integer axis, newer major → error
    "ver_l0_below_floor.zarr": {
        "l0_schema_version": 1
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
    # zarr-fits names frontend b of scan N `scan_<N + 1000000·b>` (7+ digits): the
    # validator checks that scan (here: its missing data_5d is reported).
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
    # The optional L0 completeness marker is type-checked (boolean) and holds true.
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
            f"{_SCAN}/skydiff/spectrum_0": {"dtype": "float32", "ndim": 2}
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
    # new optional L0 identity attrs; a pre-store omits them and must still
    # validate green (the fields are optional); wrong-typed values are rejected —
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
