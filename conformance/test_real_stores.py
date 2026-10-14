"""Real producer output validates green (Python binding) — #18.

The stores under ``conformance/real_stores/`` are byte-identical copies of what
zarr-fits (L0) and calibrate (L1) wrote. Every one must validate with zero
errors. A missing store fails the test; it never skips. The Rust binding
validates the same stores (bindings/rust/tests/real_stores.rs).
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
def test_real_store_is_green(name):
    store = REAL / name
    assert (store / "zarr.json").is_file(), f"real store missing: {store}"
    result = zss.check(store)
    assert result.errors == []
