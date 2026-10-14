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

import generate
import zarr_spectral_schema as zss


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


def test_l1_raw_attrs_must_be_object(stores):
    errors = zss.validate(stores / "l1_bad_raw_attrs.zarr")
    assert any(
        "raw_attrs" in e and "scan_000001" in e and "must be of type 'object'" in e
        for e in errors
    ), errors


# ── 2. reject broken output ────────────────────────────────────────────────


def test_scan_with_seven_digit_name_is_checked(stores):
    errors = zss.validate(stores / "l0_wide_scan.zarr")
    assert any("scan_1003537/source/data_5d" in e for e in errors), errors


def test_root_child_not_matching_scan_pattern_is_rejected(stores):
    errors = zss.validate(stores / "l0_bad_scan_name.zarr")
    assert any("'scan_12'" in e and "scan pattern" in e for e in errors), errors


def test_l1_complete_false_is_rejected(stores):
    errors = zss.validate(stores / "l1_incomplete.zarr")
    assert any("l1_complete" in e and "must equal true" in e for e in errors), errors


@pytest.mark.parametrize(
    "name, phrase",
    [
        ("l0_bad_l0_complete.zarr", "must be of type 'boolean'"),
        ("l0_incomplete.zarr", "must equal true"),
    ],
)
def test_l0_complete_marker_is_checked(stores, name, phrase):
    errors = zss.validate(stores / name)
    assert any("l0_complete" in e and phrase in e for e in errors), errors


def test_l0_complete_marker_true_is_green(stores):
    view = zss._store.read_store(stores / "l0.zarr")  # type: ignore[attr-defined]
    assert view.group_attrs["scan_000001"]["l0_complete"] is True
    assert zss.L0_COMPLETE_ATTR == "l0_complete"


def test_skydiff_family_member_dtype_and_ndim_are_checked(stores):
    errors = zss.validate(stores / "l1_bad_skydiff.zarr")
    rel = "scan_000001/skydiff/spectrum_0"
    assert any(rel in e and "dtype" in e for e in errors), errors
    assert any(rel in e and "ndim" in e for e in errors), errors


# ── 3. one strict parse path in both bindings ──────────────────────────────
# Each fixture is a former divergence between the Rust and the Python verdict.
# The messages asserted here are identical in both bindings.


def test_non_finite_attr_is_its_own_error(stores):
    errors = zss.validate(stores / "l1_nan_attr.zarr")
    assert "[L1] 'scan_000001/zarr.json': non-finite number in metadata" in errors, (
        errors
    )


def test_malformed_zarr_json_is_an_error_not_an_exception(stores):
    errors = zss.validate(stores / "l1_malformed_meta.zarr")
    assert (
        "[L1] 'scan_000001/t_int/zarr.json': malformed metadata (not valid JSON)"
        in errors
    )


def test_object_form_of_data_type_is_accepted(stores):
    assert zss.validate(stores / "l1_object_dtype.zarr") == []


def test_group_at_array_path_is_rejected(stores):
    errors = zss.validate(stores / "l1_group_at_array.zarr")
    assert "[L1] 'scan_000001/lloadsn' is a group, expected an array" in errors, errors


def test_unicode_digits_in_version_are_rejected(stores):
    errors = zss.validate(stores / "ver_unicode_digits.zarr")
    assert any(
        "cal_schema_version" in e and "cannot parse version" in e for e in errors
    ), errors


def test_unicode_digits_in_scan_name_are_rejected(stores):
    errors = zss.validate(stores / "l0_unicode_scan_name.zarr")
    assert any("'scan_٠٠٠٠٠٢'" in e and "scan pattern" in e for e in errors), errors


# ── structure findings carry the [<level>] prefix ──────────────────────────
# calibrate (kalibrate#82) splits check() errors by this prefix: a structure
# finding only warns there; any other error fails the run. Every structure
# finding must therefore start with "[L0] " or "[L1] ".

STRUCTURE_NEGATIVES = [
    "l0_both_groups.zarr",
    "l0_no_group.zarr",
    "l0_wide_scan.zarr",
    "l0_bad_scan_name.zarr",
    "l0_unicode_scan_name.zarr",
    "l0_bad_l0_complete.zarr",
    "l0_incomplete.zarr",
    "l1_incomplete.zarr",
    "l1_bad_raw_attrs.zarr",
    "l1_bad_skydiff.zarr",
    "l1_nan_attr.zarr",
    "l1_malformed_meta.zarr",
    "l1_group_at_array.zarr",
]


@pytest.mark.parametrize("name", STRUCTURE_NEGATIVES)
def test_structure_findings_carry_the_level_prefix(stores, name):
    errors = zss.validate(stores / name)
    assert errors, f"{name}: expected structure findings"
    assert all(e.startswith(("[L0] ", "[L1] ")) for e in errors), errors
