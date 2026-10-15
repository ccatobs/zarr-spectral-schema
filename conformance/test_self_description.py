"""Dimensions, units, flag bits and the scientific frame (Python binding) — #19.

Each test validates a spec-emitted store through the public entry point and
checks the verdict. The Rust binding runs the same checks over the same stores
(bindings/rust/tests/self_description.rs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))
sys.path.insert(0, str(_ROOT / "conformance"))

import generate
import zarr_spectral_schema as zss


@pytest.fixture(scope="module")
def stores(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("self_description")
    generate.emit_l0(out / "l0.zarr")
    generate.emit_l1_single(out / "l1_single.zarr")
    generate.emit_l1_multi(out / "l1_multi.zarr")
    generate.emit_self_description_fixtures(out)
    return out


# ── 1. dimensions and units per array ──────────────────────────────────────


@pytest.mark.parametrize("name", ["l0.zarr", "l1_single.zarr", "l1_multi.zarr"])
def test_spec_emitted_stores_are_green(stores, name):
    assert zss.validate(stores / name) == []


def test_array_without_dimension_names_is_rejected(stores):
    errors = zss.validate(stores / "l1_no_dimension_names.zarr")
    assert any(
        "'scan_000001/spectra'" in e and "no dimension_names" in e for e in errors
    ), errors


def test_dimension_names_differing_from_dims_is_rejected(stores):
    errors = zss.validate(stores / "l1_wrong_dimension_names.zarr")
    assert any(
        "'scan_000001/spectra'" in e and "dimension_names" in e and "!= expected" in e
        for e in errors
    ), errors


def test_array_without_its_declared_units_is_rejected(stores):
    errors = zss.validate(stores / "l1_no_units.zarr")
    assert any("'scan_000001/spectra'" in e and "no units attr" in e for e in errors), (
        errors
    )


def test_array_with_other_units_is_rejected(stores):
    errors = zss.validate(stores / "l1_wrong_units.zarr")
    assert any(
        "'scan_000001/spectra'" in e and 'units "mK" != expected "K"' in e
        for e in errors
    ), errors


def test_arrays_disagreeing_on_a_shared_dimension_are_rejected(stores):
    # tau_signal has 2 channels, every other channel axis at the target has 1.
    errors = zss.validate(stores / "l1_channel_mismatch.zarr")
    assert any(
        "'scan_000001'" in e and "dimension 'channel'" in e and "2 in [tau_signal]" in e
        for e in errors
    ), errors


def test_pixel_offset_and_spectra_disagreeing_on_subscans_are_rejected(stores):
    # The named case of #19: LFA_OTF/scan_025652 kept the 3 L0 subscans in
    # pixel_offset_lon while spectra holds the ON subscans only.
    errors = zss.validate(stores / "l1_pixel_offset_subscans.zarr")
    assert any(
        "dimension 'subscan'" in e
        and "3 in [pixel_offset_lon]" in e
        and "spectra" in e.split("3 in")[0]
        for e in errors
    ), errors


@pytest.mark.parametrize(
    "name, member",
    [
        ("l0_valid_dumps_subscans.zarr", "source/n_valid_dumps"),
        ("l1_valid_dumps_subscans.zarr", "n_valid_dumps"),
    ],
)
def test_valid_dump_count_disagreeing_on_subscans_is_rejected(stores, name, member):
    # #20 deferred this length check of n_valid_dumps against S to #19.
    errors = zss.validate(stores / name)
    assert any(
        "'scan_000001'" in e and "dimension 'subscan'" in e and f"3 in [{member}]" in e
        for e in errors
    ), errors


# ── 2. flag bits ───────────────────────────────────────────────────────────

# The bit table of calibrate cal-core/src/flags.rs and YODA io/flags.py: what
# real stores carry. Bit 5 is ELEVATION_LOW (ARCHITECTURE.md says BASELINE_EXCL).
# The Rust binding pins the same table (bindings/rust/tests/self_description.rs).
FLAG_BITS = {
    "BAD_CHANNEL": 1 << 0,
    "FILL_VALUE": 1 << 1,
    "SPIKE": 1 << 2,
    "RFI": 1 << 3,
    "TSYS_OUTLIER": 1 << 4,
    "ELEVATION_LOW": 1 << 5,
    "ML_FLAGGED": 1 << 6,
    "MANUAL": 1 << 7,
    "LOW_TRANSMISSION": 1 << 8,
}


def test_one_constant_per_flag_bit_in_the_spec():
    assert set(zss.load_spec()["flag_bits"]) == set(FLAG_BITS)
    assert {name: getattr(zss, name) for name in FLAG_BITS} == FLAG_BITS


# ── 3. scientific frame ────────────────────────────────────────────────────


def test_scan_without_the_frame_set_names_each_missing_field(stores):
    errors = zss.validate(stores / "l1_no_frame.zarr")
    missing = [f"attribute '{n}' is missing" for n in generate.FRAME_ATTRS] + [
        f"array 'scan_000001/{n}' is missing" for n in generate.FRAME_ARRAYS
    ]
    assert [m for m in missing if not any(m in e for e in errors)] == [], errors


def test_l0_store_without_the_site_location_is_rejected(stores):
    errors = zss.validate(stores / "l0_no_site.zarr")
    for name in ("site_longitude_deg", "site_latitude_deg", "site_altitude_m"):
        assert f"[L0] required attribute '{name}' is missing at 'root'" in errors, (
            errors
        )


# Fields that admit the not-applicable sentinel, with the sentinel they hold in
# the l1_not_applicable fixture: "NONE" for a string, JSON null for a number.
NOT_APPLICABLE = {
    "specsys": "NONE",
    "veldef": "NONE",
    "vlsr_source": None,
    "vlsr_source_frame": "NONE",
    "coord_frame": "NONE",
    "equinox": None,
    "projection": "NONE",
    "offset_convention": "NONE",
    "temperature_scale": "NONE",
    "eta_mb": None,
    "sideband_mode": "NONE",
    "image_sideband_fraction": None,
    "site_longitude_deg": None,
    "site_latitude_deg": None,
    "site_altitude_m": None,
}


def test_fixture_holds_the_not_applicable_sentinel_and_is_green(stores):
    store = stores / "l1_not_applicable.zarr"
    scan = zss._store.read_store(store).group_attrs["scan_000001"]  # type: ignore[attr-defined]
    assert {n: scan[n] for n in NOT_APPLICABLE} == NOT_APPLICABLE
    # A frame array that does not apply holds its NaN fill value.
    meta = json.loads(
        (store / "scan_000001" / "platform_lon" / "zarr.json").read_text()
    )
    assert meta["fill_value"] == "NaN"
    assert zss.validate(store) == []


def test_l0_fixture_holds_the_site_sentinel_and_is_green(stores):
    store = stores / "l0_not_applicable.zarr"
    root = zss._store.read_store(store).root_attrs  # type: ignore[attr-defined]
    assert [
        root[n] for n in ("site_longitude_deg", "site_latitude_deg", "site_altitude_m")
    ] == [
        None,
        None,
        None,
    ]
    assert zss.validate(store) == []


def test_sentinel_on_a_field_that_admits_none_is_rejected(stores):
    # eta_fss is the forward efficiency calibrate applied: it always applies.
    errors = zss.validate(stores / "l1_eta_fss_null.zarr")
    assert any("'eta_fss'" in e and "must be of type 'number'" in e for e in errors), (
        errors
    )


def test_value_outside_the_declared_set_is_rejected(stores):
    errors = zss.validate(stores / "l1_bad_specsys.zarr")
    assert any(
        "'specsys'" in e
        and "must be one of" in e
        and '"LSRK"' in e
        and 'got "LSR"' in e
        for e in errors
    ), errors


# ── 4. multi-frontend layout ───────────────────────────────────────────────

# Identity, quality record and provenance of one band: on each band group of a
# multi-frontend L1 scan, never on the scan group.
BAND_PLACED = (
    "arrays",
    "pixels_per_array",
    "frontend",
    "raw_attrs",
    "qa",
    "pwv_mm",
    "cal_hash",
    "source_l0_store",
    "source_l0_scans",
    "cal_config",
    "processing_timestamp",
    "atm_table_info",
    "processing_steps",
    "linked_pwv_source",
)


def test_identity_quality_record_and_provenance_sit_on_each_band(stores):
    view = zss._store.read_store(stores / "l1_multi.zarr")  # type: ignore[attr-defined]
    for band in ("band_0", "band_1"):
        attrs = view.group_attrs[f"scan_000001/{band}"]
        assert [n for n in BAND_PLACED if n not in attrs] == [], band
    assert [n for n in BAND_PLACED if n in view.group_attrs["scan_000001"]] == []
    assert zss.validate(stores / "l1_multi.zarr") == []


def test_band_without_its_quality_record_is_rejected(stores):
    errors = zss.validate(stores / "l1_multi_band_without_qa.zarr")
    assert (
        "[L1] required attribute 'qa' is missing at 'scan_000001/band_1'" in errors
    ), errors


def test_multi_frontend_scan_group_holding_arrays_is_rejected(stores):
    errors = zss.validate(stores / "l1_multi_scan_arrays.zarr")
    assert any(
        "attribute 'arrays' at 'scan_000001'" in e and "each band" in e for e in errors
    ), errors


def test_l0_multi_frontend_store_holds_each_band_under_its_scan(stores):
    # scan_N/band_B/ at L0, as at L1: no virtual scan number N + 1000000·b.
    view = zss._store.read_store(stores / "l0_multi.zarr")  # type: ignore[attr-defined]
    for scan, group in (("scan_000001", "source"), ("scan_000002", "calibration")):
        for band in ("band_0", "band_1"):
            assert f"{scan}/{band}/{group}/data_5d" in view.arrays
            assert "frontend" in view.group_attrs[f"{scan}/{band}"]
        assert "frontend" not in view.group_attrs[scan]
    assert zss.validate(stores / "l0_multi.zarr") == []


def test_validator_checks_every_band_of_a_multi_frontend_l0_store(stores):
    errors = zss.validate(stores / "l0_multi_band_without_data.zarr")
    assert (
        "[L0] required array 'scan_000001/band_1/source/data_5d' is missing" in errors
    ), errors


# ── every new structure finding carries the [<level>] prefix ───────────────
# calibrate (kalibrate#82) splits check() errors by this prefix.

SELF_DESCRIPTION_NEGATIVES = [
    "l1_no_dimension_names.zarr",
    "l1_wrong_dimension_names.zarr",
    "l1_no_units.zarr",
    "l1_wrong_units.zarr",
    "l1_channel_mismatch.zarr",
    "l1_pixel_offset_subscans.zarr",
    "l1_no_frame.zarr",
    "l0_no_site.zarr",
    "l1_eta_fss_null.zarr",
    "l1_bad_specsys.zarr",
    "l1_multi_band_without_qa.zarr",
    "l1_multi_scan_arrays.zarr",
    "l0_multi_band_without_data.zarr",
    "l0_no_l0_complete.zarr",
    "l1_no_identity.zarr",
    "l0_valid_dumps_subscans.zarr",
    "l1_valid_dumps_subscans.zarr",
]


@pytest.mark.parametrize("name", SELF_DESCRIPTION_NEGATIVES)
def test_new_structure_findings_carry_the_level_prefix(stores, name):
    errors = zss.validate(stores / name)
    assert errors, f"{name}: expected structure findings"
    assert all(e.startswith(("[L0] ", "[L1] ")) for e in errors), errors


# ── a malformed shape is a finding, never an exception (PR 25 review) ──────
# Both bindings pin the same full error list, so their verdicts agree.


@pytest.mark.parametrize("name", sorted(generate.BAD_SHAPES))
def test_malformed_shape_is_one_finding_in_both_bindings(stores, name):
    errors = zss.validate(stores / f"l1_shape_{name}.zarr")
    assert errors == [
        "[L1] array 'scan_000001/t_int' shape is not a list of non-negative integers"
    ]


# ── required since L0 3 / L1 2.0 (maintainer decisions of 2026-10-14) ──────


def test_l0_scan_without_its_completeness_marker_is_rejected(stores):
    errors = zss.validate(stores / "l0_no_l0_complete.zarr")
    assert (
        "[L0] required attribute 'l0_complete' is missing at 'scan_000001'" in errors
    ), errors


@pytest.mark.parametrize("name", ["frontend", "raw_attrs"])
def test_l1_scan_without_its_identity_is_rejected(stores, name):
    errors = zss.validate(stores / "l1_no_identity.zarr")
    assert f"[L1] required attribute '{name}' is missing at 'scan_000001'" in errors, (
        errors
    )
