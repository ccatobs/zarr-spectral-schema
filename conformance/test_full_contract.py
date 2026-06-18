"""Full L0+L1 structural-contract conformance test (Python binding) — Slice 4 (#5).

The validator v2 detects a store's level from its root ``detect`` attr and walks
the spec's ``placement``/``inline`` group fields and per-level ``*_attrs`` sections
generically. These tests pin that behaviour over spec-emitted stores carrying the
real on-disk hierarchy (``scan_<NNNNNN>`` groups, the L0 ``source``/``calibration``
sub-groups, multi-frontend ``band_<M>`` sub-groups):

  * each shape (L0, L1 single-frontend, L1 multi-frontend) validates green;
  * structural negatives are rejected naming the offending path/attr;
  * the scan-or-band placement rule is proven data-driven: ``line``/``rest_freq_hz``
    sit at the scan group for single-frontend and at each band for multi-frontend,
    and both stores validate green.

A final test validates the REAL writer-produced L1 store if the fixture is present
(skipped otherwise so CI without it still passes).

The Rust binding runs the equivalent checks over the SAME emitted stores
(bindings/rust/tests/conformance.rs) — together they prove one spec drives one
interpretation in both languages.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))
sys.path.insert(0, str(_ROOT / "conformance"))

import generate  # noqa: E402
import zarr_spectral_schema as zss  # noqa: E402


def _real_store(name: str) -> Path | None:
    """Resolve ``$PARITY_TEST_DIR/l1_test_data/<name>`` if it exists.

    The real writer-produced fixtures are emitted by cal-io's ignored generators
    (`generate_l1_test_store` → synthetic_l1.zarr, `generate_multi_frontend_l1_test_store`
    → synthetic_l1_multi.zarr); point PARITY_TEST_DIR at their output base.
    """
    base = os.environ.get("PARITY_TEST_DIR")
    if not base:
        return None
    p = Path(base) / "l1_test_data" / name
    return p if p.exists() else None


@pytest.fixture(scope="module")
def stores(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("contract")
    generate.emit_l0(out / "l0.zarr")
    generate.emit_l1_single(out / "l1_single.zarr")
    generate.emit_l1_multi(out / "l1_multi.zarr")
    # Negatives.
    generate.emit_l1_single(out / "miss_array.zarr", omit={"scan_000001/spectra"})
    generate.emit_l1_single(out / "miss_sob_attr.zarr", omit_attrs={"l1_complete"})
    generate.emit_l1_multi(out / "miss_band_index.zarr", omit_band_attrs={1: {"band_index"}})
    return out


# ── positive: each shape validates green ───────────────────────────────────


def test_l0_store_is_green(stores):
    result = zss.check(stores / "l0.zarr")
    assert result.ok, result.errors


def test_l1_single_frontend_is_green(stores):
    result = zss.check(stores / "l1_single.zarr")
    assert result.ok, result.errors


def test_l1_multi_frontend_is_green(stores):
    result = zss.check(stores / "l1_multi.zarr")
    assert result.ok, result.errors


# ── negatives: structural failures name the offending path/attr ────────────


def test_missing_required_array_named(stores):
    errors = zss.validate(stores / "miss_array.zarr")
    assert any("scan_000001/spectra" in e for e in errors), errors


def test_missing_required_scan_or_band_attr_named(stores):
    # l1_complete is a required scan_or_band attr (the completeness marker).
    errors = zss.validate(stores / "miss_sob_attr.zarr")
    assert any("l1_complete" in e for e in errors), errors


def test_multi_missing_band_index_named(stores):
    # band_index is a required per-band attr; dropping it on band_1 must error
    # naming that band group specifically.
    errors = zss.validate(stores / "miss_band_index.zarr")
    assert any("band_index" in e and "band_1" in e for e in errors), errors


def test_store_with_no_level_is_rejected(tmp_path):
    # A bare group carrying neither detect attr declares no known level.
    import zarr

    bare = tmp_path / "bare.zarr"
    zarr.open_group(store=str(bare), mode="w", zarr_format=3)
    errors = zss.validate(bare)
    assert any("no known level" in e for e in errors), errors


# ── the scan-or-band rule is DATA-driven, not hand-branched ────────────────


def test_scan_or_band_rule_is_data_driven(stores):
    """line/rest_freq_hz are accepted at band level (multi) and scan level (single).

    Both stores carry the same `scan_or_band_attrs` declaration; the validator
    resolves the target from the presence of band_<M> children — never a special
    case — so a single-frontend store with these attrs at the scan group and a
    multi-frontend store with them on each band both validate green.
    """
    single = zss.check(stores / "l1_single.zarr")
    multi = zss.check(stores / "l1_multi.zarr")
    assert single.ok, single.errors
    assert multi.ok, multi.errors

    # Prove the placement actually differs on disk: single carries line on the
    # scan group; multi carries it on each band, not on the scan group.
    view_single = zss._store.read_store(stores / "l1_single.zarr")  # type: ignore[attr-defined]
    view_multi = zss._store.read_store(stores / "l1_multi.zarr")  # type: ignore[attr-defined]
    assert "line" in view_single.group_attrs["scan_000001"]
    assert "line" not in view_multi.group_attrs["scan_000001"]
    assert "line" in view_multi.group_attrs["scan_000001/band_0"]
    assert "line" in view_multi.group_attrs["scan_000001/band_1"]


# ── real writer-produced stores (skipped if the fixture is absent) ─────────
#
# The correctness gate: a real cal-io store (not spec-emitted) must validate
# green, so the spec's required flags match what the writer always writes.


def test_real_single_frontend_store():
    real = _real_store("synthetic_l1.zarr")
    if real is None:
        pytest.skip(
            "real single-frontend L1 fixture absent; generate with "
            "`cargo test -p cal-io --test generate_l1_test_store -- --ignored` "
            "and set PARITY_TEST_DIR"
        )
    result = zss.check(real)
    assert result.ok, result.errors
    assert result.errors == []


def test_real_multi_frontend_store():
    real = _real_store("synthetic_l1_multi.zarr")
    if real is None:
        pytest.skip(
            "real multi-frontend L1 fixture absent; generate with "
            "`cargo test -p cal-io --test generate_multi_frontend_l1_test_store -- --ignored` "
            "and set PARITY_TEST_DIR"
        )
    result = zss.check(real)
    assert result.ok, result.errors
    assert result.errors == []
