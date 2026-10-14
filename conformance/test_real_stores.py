"""Real producer output is read cleanly (Python binding) — #18, #19.

The stores under ``conformance/real_stores/`` are byte-identical copies of what
zarr-fits (L0) and calibrate (L1) wrote. #19 bumped the contract to L0 3 and
L1 2.0, so these stores (L0 2, L1 1.3) sit below ``min_supported``: each must
get exactly one version error, the below-floor error, and no metadata error
(the validator still parses every node). A missing store fails the test; it
never skips. The Rust binding validates the same stores
(bindings/rust/tests/real_stores.rs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))

import zarr_spectral_schema as zss

REAL = _ROOT / "conformance" / "real_stores"

# One single-frontend pair and one multi-frontend pair (see real_stores/README.md),
# each with the version attr of its level.
REAL_STORES = {
    "zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr": "l0_schema_version",
    "calibrate/SINGLE_POINT_TP": "cal_schema_version",
    "zarr/SOFIA_UPGREAT_ORION_KL.zarr": "l0_schema_version",
    "calibrate/ORION_KL": "cal_schema_version",
}


@pytest.mark.parametrize("name, attr", REAL_STORES.items())
def test_real_store_gets_the_below_floor_error(name, attr):
    store = REAL / name
    assert (store / "zarr.json").is_file(), f"real store missing: {store}"
    errors = zss.validate(store)
    # Version errors carry no [<level>] prefix; structure findings do.
    version_errors = [e for e in errors if not e.startswith("[")]
    assert len(version_errors) == 1, version_errors
    assert attr in version_errors[0], version_errors
    assert "older than the minimum supported" in version_errors[0], version_errors
    assert not any("metadata" in e for e in errors), errors
