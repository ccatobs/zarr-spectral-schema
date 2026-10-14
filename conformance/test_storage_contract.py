"""Object storage and production volume (Python binding) — #20.

Each test validates a spec-emitted store (``generate.emit_storage_fixtures``)
through the public entry point and checks the verdict. The Rust binding runs the
same checks over the same stores (bindings/rust/tests/storage_contract.rs).
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
    assert any("scan_000001/spectra" in e and "'int16'" in e for e in errors), errors


def test_l1_integer_keepbits_is_green(stores):
    assert zss.validate(stores / "l1_keepbits.zarr") == []


def test_l1_non_integer_keepbits_is_rejected(stores):
    errors = zss.validate(stores / "l1_bad_keepbits.zarr")
    assert any(
        "keepbits" in e and "scan_000001/spectra" in e and "'integer'" in e
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
