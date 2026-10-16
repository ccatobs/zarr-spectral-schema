"""Identity + provenance contract conformance (Python binding) — Slice 5a (#13).

The spec gains an additive pixel/source identity + provenance surface: L0 scan
attrs `frontend` (string), `pixels_per_array` (list-of-lists `array`), and
`raw_attrs` (`object`); `frontend` promoted at L1 from a band attr to a
scan-or-band attr; and a validator-only `precedence_aliases` block documenting
`pixel_offset: { from: [PIXOFFX, RXDX] }`. #13 added the fields as
`required: false`; L0 3 (#19) made the L0 identity attrs required:

  * a post-#6 store carrying the new fields validates green (and the fields land
    at the declared placement);
  * a pre-#6 store WITHOUT the identity attrs is rejected, naming each one;
  * wrong-typed values are rejected — `raw_attrs` as a string, and the legacy
    `pixels_per_array` HashMap/dict form (which `type: array` deliberately
    refuses, so the non-deterministic dict representation cannot be lifted).

The Rust binding runs the equivalent checks over the same spec-emitted stores
(bindings/rust/tests/identity_contract.rs) — the cross-binding parity leg.
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
    out = tmp_path_factory.mktemp("identity")
    # Post-#6: the default L0 store now carries frontend/pixels_per_array/raw_attrs.
    generate.emit_l0(out / "l0_post6.zarr")
    # Pre-#6: the same store without the new optional identity attrs.
    generate.emit_l0(out / "l0_pre6.zarr", omit_attrs=set(generate._IDENTITY_L0_ATTRS))
    # Wrong-typed negatives. The raw_attrs bad value is `42` (no "object"
    # substring) so the assertion can only pass if the validator names the
    # expected type, not because the value text happened to contain it.
    generate.emit_l0(
        out / "l0_bad_raw_attrs.zarr", scan_attr_overrides={"raw_attrs": 42}
    )
    generate.emit_l0(
        out / "l0_bad_pixels.zarr",
        scan_attr_overrides={"pixels_per_array": {"HFA": ["P1", "P2"]}},
    )
    # L1 shapes (frontend now a scan-or-band attr).
    generate.emit_l1_single(out / "l1_single.zarr")
    generate.emit_l1_multi(out / "l1_multi.zarr")
    return out


# ── additive: post-#6 green AND the new fields land at the declared placement ──


def test_post6_l0_validates_green(stores):
    result = zss.check(stores / "l0_post6.zarr")
    assert result.ok, result.errors


def test_post6_l0_carries_identity_attrs_at_scan_group(stores):
    view = zss._store.read_store(stores / "l0_post6.zarr")  # type: ignore[attr-defined]
    scan = view.group_attrs["scan_000001"]
    assert "frontend" in scan
    assert "pixels_per_array" in scan
    assert "raw_attrs" in scan
    # list-of-lists representation, never the legacy dict.
    assert isinstance(scan["pixels_per_array"], list)
    assert isinstance(scan["raw_attrs"], dict)


# ── L0 3 (#19): the identity attrs are required ────────────────────────────────


def test_pre6_l0_without_identity_attrs_is_rejected(stores):
    errors = zss.validate(stores / "l0_pre6.zarr")
    for name in sorted(generate._IDENTITY_L0_ATTRS):
        assert (
            f"[L0] required attribute '{name}' is missing at 'scan_000001'" in errors
        ), errors


# ── wrong-typed values are rejected ────────────────────────────────────────────


def test_raw_attrs_must_be_object(stores):
    # Assert the type phrase (not just "object", which would match the value
    # text) so this proves a type-error rejection, not merely "an error names it".
    errors = zss.validate(stores / "l0_bad_raw_attrs.zarr")
    assert any("raw_attrs" in e and 'must be of type "object"' in e for e in errors), (
        errors
    )


def test_pixels_per_array_dict_form_is_rejected(stores):
    # The legacy HashMap/dict representation must NOT validate: `type: array`
    # rejects a JSON object, so the non-deterministic dict cannot be lifted.
    # Assert the type phrase — "array" alone is a substring of the attribute
    # name pixels_per_array, so it would pass vacuously.
    errors = zss.validate(stores / "l0_bad_pixels.zarr")
    assert any(
        "pixels_per_array" in e and 'must be of type "array"' in e for e in errors
    ), errors


# ── L1: frontend promoted to scan-or-band placement ────────────────────────────


def test_frontend_is_scan_or_band_at_l1(stores):
    """frontend sits at the scan group (single-frontend) and each band (multi)."""
    single = zss.check(stores / "l1_single.zarr")
    multi = zss.check(stores / "l1_multi.zarr")
    assert single.ok, single.errors
    assert multi.ok, multi.errors

    view_single = zss._store.read_store(stores / "l1_single.zarr")  # type: ignore[attr-defined]
    view_multi = zss._store.read_store(stores / "l1_multi.zarr")  # type: ignore[attr-defined]
    # Single-frontend: frontend on the scan group itself.
    assert "frontend" in view_single.group_attrs["scan_000001"]
    # Multi-frontend: frontend on each band, not on the scan group.
    assert "frontend" not in view_multi.group_attrs["scan_000001"]
    assert "frontend" in view_multi.group_attrs["scan_000001/band_0"]
    assert "frontend" in view_multi.group_attrs["scan_000001/band_1"]


# ── precedence_aliases: validator-only spec data (no Rust const) ────────────────


def test_precedence_aliases_documents_pixel_offset():
    spec = zss.load_spec()
    aliases = spec["precedence_aliases"]
    assert aliases["pixel_offset"]["from"] == ["PIXOFFX", "RXDX"]
