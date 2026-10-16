"""Object storage and production volume (Python binding) — #20.

Each test validates a spec-emitted store (``generate.emit_storage_fixtures``)
through the public entry point and checks the verdict. The Rust binding runs the
same checks over the same stores (bindings/rust/tests/storage_contract.rs).
"""

from __future__ import annotations

import json
import os
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
    out = tmp_path_factory.mktemp("storage")
    generate.emit_storage_fixtures(out)
    return out


# ── 1. chunk independence ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, rel",
    [
        ("l0_sharded.zarr", "scan_000001/source/data_5d"),
        ("l1_sharded.zarr", "scan_000001/spectra"),
    ],
)
def test_store_with_non_default_chunk_layout_is_green(stores, name, rel):
    # The fixture really holds the recommended layout, not the writer default
    # (one chunk per array): one shard per pixel and subscan (C, D, 1, 1, 1)
    # with inner chunks.
    meta = json.loads((stores / name / rel / "zarr.json").read_text())
    assert meta["chunk_grid"]["configuration"]["chunk_shape"] == [2, 2, 1, 1, 1]
    assert meta["shape"] == [2, 2, 2, 2, 2]
    assert meta["codecs"][0]["name"] == "sharding_indexed"
    assert meta["codecs"][0]["configuration"]["chunk_shape"] == [1, 2, 1, 1, 1]
    assert zss.validate(stores / name) == []


# ── 3. dtype choice ────────────────────────────────────────────────────────


def test_l1_float32_spectra_and_t_sys_are_green(stores):
    assert zss.validate(stores / "l1_float32.zarr") == []


def test_l1_float64_spectra_and_t_sys_are_green(stores):
    assert zss.validate(stores / "l1_float64.zarr") == []


def test_l1_int16_spectra_is_rejected(stores):
    errors = zss.validate(stores / "l1_int16_spectra.zarr")
    assert any("scan_000001/spectra" in e and '"int16"' in e for e in errors), errors


def test_l1_integer_keepbits_is_green(stores):
    assert zss.validate(stores / "l1_keepbits.zarr") == []


def test_l1_non_integer_keepbits_is_rejected(stores):
    errors = zss.validate(stores / "l1_bad_keepbits.zarr")
    assert any(
        "keepbits" in e and "scan_000001/spectra" in e and '"integer"' in e
        for e in errors
    ), errors


# ── 4. valid dump count ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, rel",
    [
        ("l0_bad_valid_dumps.zarr", "scan_000001/source/n_valid_dumps"),
        ("l1_bad_valid_dumps.zarr", "scan_000001/n_valid_dumps"),
    ],
)
def test_valid_dump_count_dtype_and_dims_are_checked(stores, name, rel):
    errors = zss.validate(stores / name)
    assert any(rel in e and "dtype" in e for e in errors), errors
    assert any(rel in e and "ndim" in e for e in errors), errors


# ── 7. one writer per store: source_l0_store holds a URI reference ────────


@pytest.mark.parametrize("name", sorted(generate.SOURCE_L0_STORE_FIXTURES))
def test_source_l0_store_is_a_uri_reference(stores, name):
    _, ok = generate.SOURCE_L0_STORE_FIXTURES[name]
    errors = zss.validate(stores / name)
    if ok:
        assert errors == [], errors
    else:
        assert any("source_l0_store" in e and "URI reference" in e for e in errors), (
            errors
        )


# ── 6. one local walk, one input grammar ───────────────────────────────────


def test_symlinked_scan_is_not_followed(stores):
    # scan_000002 links to a scan without spectra; the walk does not enter it.
    assert (stores / "l1_symlinked_scan.zarr" / "scan_000002").is_symlink()
    assert zss.validate(stores / "l1_symlinked_scan.zarr") == []


def test_local_path_with_double_colon_is_that_path(stores):
    errors = zss.validate(stores / "a::b" / "miss_array.zarr")
    assert errors == ["[L1] required array 'scan_000001/spectra' is missing"]


def test_unlistable_local_group_is_an_error(tmp_path):
    store = generate.emit_l1_single(
        tmp_path / "l1_bad_skydiff.zarr",
        array_overrides={
            "scan_000001/skydiff/spectrum_0": {
                "dtype": "float32",
                "ndim": 2,
                "dims": ["channel", "receiver"],
            }
        },
    )
    skydiff = store / "scan_000001" / "skydiff"
    skydiff.chmod(0o311)  # no read: the directory cannot be listed
    if os.access(skydiff, os.R_OK):  # root lists it anyway: the case cannot arise
        skydiff.chmod(0o755)
        pytest.skip("a 0311 directory is listable (running as root)")
    try:
        errors = zss.validate(store)
    finally:
        skydiff.chmod(0o755)
    assert errors == ["[L1] 'scan_000001/skydiff/': cannot list"]


# ── 8. L0 counts: uint32 with a saturated-counter fill value ──────────────

L0_COUNTS = "scan_000001/source/data_5d"


def test_l0_counts_are_uint32_with_the_saturated_fill_value(stores):
    meta = json.loads(
        (stores / "l0_sharded.zarr" / L0_COUNTS / "zarr.json").read_text()
    )
    assert meta["data_type"] == "uint32"
    assert meta["fill_value"] == 4294967295


def test_l0_int32_counts_are_a_dtype_error_naming_data_5d(stores):
    errors = zss.validate(stores / "l0_int32_counts.zarr")
    assert any(L0_COUNTS in e and "dtype" in e and "uint32" in e for e in errors), (
        errors
    )


def test_l0_wrong_fill_value_names_the_array_expected_and_found(stores):
    errors = zss.validate(stores / "l0_bad_fill_value.zarr")
    assert any(
        L0_COUNTS in e and "fill_value 0 != expected 4294967295" in e for e in errors
    ), errors
    assert all(e.startswith("[L0] ") for e in errors), errors


def test_l0_float_fill_value_is_a_mismatch(stores):
    errors = zss.validate(stores / "l0_float_fill_value.zarr")
    assert any(
        L0_COUNTS in e and "fill_value 4294967295.0 != expected 4294967295" in e
        for e in errors
    ), errors


# ── 9. one FITS header per array; gamma in counts per second per kelvin ───


PIXEL_HEADERS = ["raw_fits_headers", "frontend_backends"]


@pytest.mark.parametrize("name", PIXEL_HEADERS)
def test_l0_headers_per_pixel_are_green(stores, name):
    # l0_sharded has R = A = S = 2: the headers hold one cell per pixel and subscan.
    meta = json.loads(
        (
            stores / "l0_sharded.zarr" / f"scan_000001/source/{name}" / "zarr.json"
        ).read_text()
    )
    assert meta["shape"] == [2, 2, 2]
    assert meta["dimension_names"] == ["receiver", "array", "subscan"]
    assert zss.validate(stores / "l0_sharded.zarr") == []


@pytest.mark.parametrize("name", PIXEL_HEADERS)
@pytest.mark.parametrize(
    "fixture, finding",
    [
        ("l0_headers_1d.zarr", "ndim 1 != expected 3"),
        ("l0_headers_array_subscan.zarr", "ndim 2 != expected 3"),
    ],
)
def test_l0_headers_without_the_pixel_axes_are_rejected(stores, name, fixture, finding):
    errors = zss.validate(stores / fixture)
    rel = f"scan_000001/source/{name}"
    assert any(rel in e and finding in e for e in errors), errors
    assert any(rel in e and "dimension_names" in e for e in errors), errors


@pytest.mark.parametrize(
    "fixture, dim",
    [
        ("l0_headers_receiver_mismatch.zarr", "receiver"),
        ("l0_headers_array_mismatch.zarr", "array"),
    ],
)
def test_l0_headers_with_a_wrong_pixel_axis_length_are_rejected(stores, fixture, dim):
    errors = zss.validate(stores / fixture)
    assert any(
        f"dimension '{dim}'" in e and "2 in [source/frontend_backends" in e
        for e in errors
    ), errors


def test_l1_gamma_in_counts_per_kelvin_is_a_units_error(stores):
    errors = zss.validate(stores / "l1_gamma_old_unit.zarr")
    assert any(
        "scan_000001/gamma" in e and 'units "ct/K" != expected "ct/(K s)"' in e
        for e in errors
    ), errors
