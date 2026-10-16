"""Real producer output is read cleanly (Python binding) — #18, #19.

The stores under ``conformance/real_stores/`` are producer output of
ccatobs/kalibrate ``epic/zss-38`` at 53e8e8b: zarr-fits wrote L0 3, calibrate wrote
L1 2.0 (see real_stores/README.md). Each store validates with zero findings. A
missing store fails the test; it never skips. The Rust binding validates the
same stores (bindings/rust/tests/real_stores.rs).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import zarr

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


def _strings(value):
    """Every string in a JSON value, nested ones too."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def test_real_stores_hold_no_local_path():
    # A store written with absolute input paths carries them in attrs
    # (fits_folder, atm_table_info.path) and in raw_fits_headers cells
    # (original_fits_file): local paths of one machine.
    found = []
    for meta_path in sorted(REAL.rglob("zarr.json")):
        meta = json.loads(meta_path.read_text())
        node = meta_path.parent.relative_to(REAL)
        cells = list(_strings(meta.get("attributes", {})))
        if meta.get("node_type") == "array" and meta.get("data_type") == "string":
            cells += [
                str(c) for c in zarr.open_array(meta_path.parent, mode="r")[...].ravel()
            ]
        found += [(str(node), c[:80]) for c in cells if _local_path(c)]
    assert found == [], found[:5]


# A local path of one machine: an absolute POSIX path, or a home, temp or
# Windows drive path anywhere in the text.
_LOCAL_PATH = re.compile(r"^/|/home/|/Users/|/tmp/|~/|\b[A-Za-z]:\\")


def _local_path(cell: str) -> bool:
    return bool(_LOCAL_PATH.search(cell))


@pytest.mark.parametrize(
    "cell",
    [
        "/data/x.fits",
        "FILE = '/home/u/x.fits'",
        "FILE = 'x/Users/u/x.fits'",
        "FILE = 'x/tmp/x.fits'",
        "FILE = '~/x.fits'",
        "FILE = 'C:\\data\\x.fits'",
    ],
)
def test_path_guard_finds_a_local_path(cell):
    assert _local_path(cell)


def test_path_guard_passes_a_relative_path():
    assert not _local_path("FILE = 'x.fits' / DATE: 2026-10-14T12:00:00")
