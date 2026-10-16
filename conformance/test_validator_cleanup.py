"""Validator and spec findings of the contract epic (Python binding) — #33.

Each test validates a spec-emitted store (``generate.emit_cleanup_fixtures``)
through the public entry point. The Rust binding runs the same checks over the
same stores (bindings/rust/tests/validator_cleanup.rs).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))
sys.path.insert(0, str(_ROOT / "conformance"))

import generate
import zarr_spectral_schema as zss


@pytest.fixture(scope="module")
def stores(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("cleanup")
    generate.emit_cleanup_fixtures(out)
    return out


# ── 1. validator walk and error output ─────────────────────────────────────


def test_parse_error_is_reported_once(stores):
    errors = zss.validate(stores / "two_levels.zarr")
    parse = [e for e in errors if "malformed metadata" in e]
    assert parse == [
        "[L0] 'scan_000001/t_int/zarr.json': malformed metadata (not valid JSON)"
    ]


def test_version_errors_come_in_sorted_order(stores):
    errors = zss.validate(stores / "two_levels.zarr")
    versions = [e for e in errors if e.startswith("root attribute")]
    assert [v.split("'")[1] for v in versions] == [
        "cal_schema_version",
        "l0_schema_version",
    ]


def test_array_at_a_scan_path_is_a_finding(stores):
    errors = zss.validate(stores / "l0_array_at_scan_path.zarr")
    assert errors == ["[L0] 'scan_000003' is an array, expected a scan group"]


def test_validation_time_grows_linearly_with_the_scans(tmp_path):
    # 2,000 scans in memory: about 0.3 s with one child index, 20 s when each
    # target scans every node (review of PR #36).
    one = zss._store.read_store(generate.emit_l1_single(tmp_path / "l1.zarr"))  # type: ignore[attr-defined]
    view = zss._store.StoreView(root_attrs=one.root_attrs)  # type: ignore[attr-defined]
    for i in range(2000):
        scan = f"scan_{i:06d}"
        for p, a in one.group_attrs.items():
            view.group_attrs[p.replace("scan_000001", scan, 1)] = a
        for p, a in one.arrays.items():
            view.arrays[p.replace("scan_000001", scan, 1)] = a
    start = time.perf_counter()
    assert zss.validate_view(view, zss.load_spec()) == []
    assert time.perf_counter() - start < 5


# ── 2. spec checks ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, level, node",
    [
        ("l1_multi_scan_array.zarr", "L1", "scan_000001/spectra"),
        ("l0_multi_scan_group.zarr", "L0", "scan_000001/source"),
    ],
)
def test_node_at_a_multi_frontend_scan_group_is_a_finding(stores, name, level, node):
    errors = zss.validate(stores / name)
    want = (
        f"[{level}] '{node}' is not a band group: the scan group of a multi-frontend "
        "scan holds band groups only"
    )
    assert errors == [want]


def test_virtual_scan_group_is_a_finding(stores):
    errors = zss.validate(stores / "l0_virtual_scan.zarr")
    want = (
        "[L0] 'scan_1029951' is a virtual scan number (scan_029951 + 1000000 x 1): "
        "a multi-frontend scan holds each band under scan_N/band_B/"
    )
    assert errors == [want]


def test_axis_outside_the_dimension_vocabulary_is_a_finding(stores):
    errors = zss.validate(stores / "l1_dims_outside_vocabulary.zarr")
    want = (
        "[L1] array 'scan_000001/spectra' dimension_names "
        '["channel", "time", "receiver", "array", "subscan"] != expected '
        '["channel", "dump", "receiver", "array", "subscan"]'
    )
    assert errors == [want]


def test_units_attr_on_an_array_without_a_unit_is_a_finding(stores):
    errors = zss.validate(stores / "l1_units_on_flags.zarr")
    assert errors == [
        "[L1] array 'scan_000001/flags' has a units attr \"\" but declares no unit"
    ]


def test_non_ascii_text_is_not_escaped(stores):
    errors = zss.validate(stores / "l1_non_ascii_specsys.zarr")
    assert len(errors) == 1 and errors[0].endswith('got "LSRü"'), errors


def test_negative_zero_is_an_integer(stores):
    store = stores / "l1_negative_zero.zarr"
    assert '"ref_channel_base": -0' in (store / "scan_000001" / "zarr.json").read_text()
    assert zss.validate(store) == []


def test_frame_attrs_come_from_the_spec():
    spec = zss.load_spec()
    assert (
        zss.FRAME_ATTRS == tuple(spec["lists"]["FRAME_ATTRS"]) == generate.FRAME_ATTRS
    )
    level = spec["levels"]["L1"]
    declared = {**level["scan_attrs"], **level["scan_or_band_attrs"]}
    assert all(declared[n]["required"] for n in zss.FRAME_ATTRS)


@pytest.mark.parametrize(
    "declared, value",
    [
        ({"type": "number", "required": True, "enum": [1, 2]}, 1.0),
        ({"type": "number", "required": True, "equals": 1}, 1.0),
    ],
)
def test_enum_and_equals_compare_numbers_by_value(declared, value):
    result = zss.ValidationResult()
    zss._validator._check_attrs({"x": value}, {"x": declared}, "root", "L1", result)  # type: ignore[attr-defined]
    assert result.errors == []


def test_array_view_keeps_its_positional_arguments():
    # dtype, shape, attrs: the order of the first release with attrs (749a98f).
    view = zss._store.ArrayView("float64", [1], {"units": "K"})  # type: ignore[attr-defined]
    assert view.attrs == {"units": "K"} and view.dimension_names is None


# ── 5. unit comparison ─────────────────────────────────────────────────────


def test_units_compare_as_exact_strings(stores):
    # ADR 0012 (units contract): no unit parsing, `ct / (K s)` is not `ct/(K s)`.
    errors = zss.validate(stores / "l1_gamma_spaced_unit.zarr")
    assert errors == [
        '[L1] array \'scan_000001/gamma\' units "ct / (K s)" != expected "ct/(K s)"'
    ]
