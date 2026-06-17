"""Tracer-bullet conformance test (Python binding).

Emits the synthetic stores from the spec, validates the conformant one green and
the negative one (missing `data_5d`) red, and checks the spec-derived constants.
The Rust binding runs the equivalent check over the SAME emitted stores
(bindings/rust/tests/conformance.rs) — together they are the cross-binding proof.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the binding and the generator importable without installation.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))
sys.path.insert(0, str(_ROOT / "conformance"))

import generate  # noqa: E402
import zarr_spectral_schema as zss  # noqa: E402


@pytest.fixture(scope="module")
def stores(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("stores")
    generate.emit(out / "valid.zarr")
    generate.emit(out / "missing_data_5d.zarr", omit={"source/data_5d"})
    return out


def test_constants_derived_from_spec():
    # Loaded from the spec — not hand-typed — and matching the Rust binding.
    assert zss.DATA_5D == "data_5d"
    assert zss.L0_SCHEMA_VERSION == "2"
    # Expanded vocabulary (#3): groups, L1 version, marker, list consts.
    assert zss.SOURCE_GROUP == "source"
    assert zss.CALIBRATION_GROUP == "calibration"
    assert zss.L1_SCHEMA_VERSION == "1.3"  # const name ≠ UPPER(attr cal_schema_version)
    assert zss.TSKY_L0 == "tsky"  # const override
    assert zss.L1_COMPLETE_ATTR == "l1_complete"  # marker
    assert zss.SOURCE_MODES == ("ON", "OFF", "OTF-ON", "OTF-OFF")  # list const
    assert zss.CAL_COORDS[7:9] == ("thot", "tcold")  # calibration-only coords, in order


def test_valid_store_is_green(stores):
    problems = zss.validate(stores / "valid.zarr")
    assert problems == [], problems


def test_missing_data_5d_is_rejected(stores):
    problems = zss.validate(stores / "missing_data_5d.zarr")
    assert any("source/data_5d" in p for p in problems), problems
