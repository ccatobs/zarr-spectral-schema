"""Accept real producer output, reject broken output (Python binding) — #18.

Each test validates a spec-emitted store (``generate.emit_real_output_fixtures``)
through the public entry point and checks the verdict. The Rust binding runs the
same checks over the same stores (bindings/rust/tests/real_output.rs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))
sys.path.insert(0, str(_ROOT / "conformance"))

import generate  # noqa: E402
import zarr_spectral_schema as zss  # noqa: E402


@pytest.fixture(scope="module")
def stores(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("real_output")
    generate.emit_l0(out / "l0.zarr")
    generate.emit_real_output_fixtures(out)
    return out


# ── 1. a scan holds exactly one of the source group and the calibration group ──


def test_scan_with_only_source_or_only_calibration_is_green(stores):
    # l0.zarr: scan_000001 holds only source/, scan_000002 only calibration/.
    view = zss._store.read_store(stores / "l0.zarr")  # type: ignore[attr-defined]
    assert "scan_000001/source" in view.group_attrs
    assert "scan_000001/calibration" not in view.group_attrs
    assert "scan_000002/calibration" in view.group_attrs
    assert "scan_000002/source" not in view.group_attrs
    assert zss.validate(stores / "l0.zarr") == []


@pytest.mark.parametrize("name", ["l0_both_groups.zarr", "l0_no_group.zarr"])
def test_scan_with_both_or_neither_group_is_rejected(stores, name):
    errors = zss.validate(stores / name)
    assert any("scan_000001" in e and "exactly one of" in e for e in errors), errors


def test_provenance_attrs_as_objects_are_green(stores):
    # calibrate writes source_l0_scans and processing_steps as JSON objects.
    view = zss._store.read_store(stores / "l1_provenance_objects.zarr")  # type: ignore[attr-defined]
    scan = view.group_attrs["scan_000001"]
    assert isinstance(scan["source_l0_scans"], dict)
    assert isinstance(scan["processing_steps"], dict)
    assert zss.validate(stores / "l1_provenance_objects.zarr") == []
