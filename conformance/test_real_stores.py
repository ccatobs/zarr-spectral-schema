"""Real producer output is read cleanly (Python binding) — #18, #19.

The stores under ``conformance/real_stores/`` are producer output of
ccatobs/kalibrate ``epic/21`` at 5bf7c7b: zarr-fits wrote L0 3, calibrate wrote
L1 2.0 (see real_stores/README.md). Each store validates with zero findings. A
missing store fails the test; it never skips. The Rust binding validates the
same stores (bindings/rust/tests/real_stores.rs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))

import zarr_spectral_schema as zss

REAL = _ROOT / "conformance" / "real_stores"

# One single-frontend pair and one multi-frontend pair (see real_stores/README.md).
REAL_STORES = [
    "zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr",
    "calibrate/SINGLE_POINT_TP",
    "zarr/SOFIA_UPGREAT_ORION_KL.zarr",
    "calibrate/ORION_KL",
]


@pytest.mark.parametrize("name", REAL_STORES)
def test_real_store_has_zero_findings(name):
    store = REAL / name
    assert (store / "zarr.json").is_file(), f"real store missing: {store}"
    result = zss.check(store)
    assert result.errors == [], result.errors
    assert result.warnings == [], result.warnings
