//! Identity + provenance contract conformance (Rust binding) — Slice 5a (#13).
//!
//! Validates the SAME spec-emitted stores the Python binding does
//! (conformance/test_identity_contract.py) — the cross-binding parity leg. The
//! shared generator (`conformance/generate.py main()`) emits, into the target
//! dir: the post-#6 `l0.zarr` (carrying the identity attrs), the pre-#6
//! `l0_pre6.zarr` (without them), and the wrong-typed negatives
//! `l0_bad_raw_attrs.zarr` / `l0_bad_pixels_per_array.zarr`.
//!
//! Post-#6 green; pre-#6 rejected since L0 3 (#19) made the identity attrs
//! required; wrong-typed rejected — including the legacy `pixels_per_array`
//! dict form, which `type: array` deliberately refuses.

mod common;

use common::fixtures;
use zarr_spectral_schema as zss;

// ── post-#6 green; pre-#6 (no identity attrs) rejected since L0 3 ─────────────

#[test]
fn post6_l0_is_green() {
    let f = fixtures();
    let problems = zss::validate(&f.join("l0.zarr"));
    assert!(
        problems.is_empty(),
        "post-#6 store must be green, got: {problems:?}"
    );
}

#[test]
fn pre6_l0_without_identity_attrs_is_rejected() {
    // L0 3 (#19): the identity attrs are required.
    let f = fixtures();
    let problems = zss::validate(&f.join("l0_pre6.zarr"));
    for name in ["frontend", "pixels_per_array", "raw_attrs"] {
        let want = format!("[L0] required attribute '{name}' is missing at 'scan_000001'");
        assert!(
            problems.contains(&want),
            "expected {want:?}, got: {problems:?}"
        );
    }
}

// ── wrong-typed values are rejected ─────────────────────────────────────────

#[test]
fn raw_attrs_must_be_object() {
    // Assert the type phrase (the bad value is `42`, which has no "object"
    // substring) so this proves a type-error rejection, not "an error names it".
    let f = fixtures();
    let problems = zss::validate(&f.join("l0_bad_raw_attrs.zarr"));
    assert!(
        problems.iter().any(|p| p.contains("raw_attrs")
            && p.contains("must be of type")
            && p.contains("object")),
        "expected raw_attrs type rejection, got: {problems:?}"
    );
}

#[test]
fn pixels_per_array_dict_form_is_rejected() {
    // The legacy HashMap/dict form must NOT validate: `type: array` rejects a
    // JSON object, so the non-deterministic dict representation cannot be lifted.
    // Assert the type phrase — "array" alone is a substring of the attribute
    // name pixels_per_array, so it would pass vacuously.
    let f = fixtures();
    let problems = zss::validate(&f.join("l0_bad_pixels_per_array.zarr"));
    assert!(
        problems.iter().any(|p| p.contains("pixels_per_array")
            && p.contains("must be of type")
            && p.contains("array")),
        "expected pixels_per_array type rejection, got: {problems:?}"
    );
}

// ── L1: frontend promoted to scan-or-band placement ─────────────────────────

/// Read one group node's `attributes` map (parity with the Python store reader).
fn group_attrs(store: &std::path::Path, rel: &str) -> serde_json::Map<String, serde_json::Value> {
    let text = std::fs::read_to_string(store.join(rel).join("zarr.json")).expect("read zarr.json");
    let v: serde_json::Value = serde_json::from_str(&text).expect("parse zarr.json");
    v.get("attributes")
        .and_then(|a| a.as_object())
        .cloned()
        .unwrap_or_default()
}

#[test]
fn l1_frontend_scan_or_band_is_green() {
    let f = fixtures();
    assert!(zss::validate(&f.join("l1_single.zarr")).is_empty());
    assert!(zss::validate(&f.join("l1_multi.zarr")).is_empty());
}

#[test]
fn l1_frontend_placement_matches_python() {
    // On-disk placement parity with the Python test: frontend sits at the scan
    // group for single-frontend, and on each band (not the scan group) for multi.
    let f = fixtures();

    let single = f.join("l1_single.zarr");
    assert!(group_attrs(&single, "scan_000001").contains_key("frontend"));

    let multi = f.join("l1_multi.zarr");
    assert!(!group_attrs(&multi, "scan_000001").contains_key("frontend"));
    assert!(group_attrs(&multi, "scan_000001/band_0").contains_key("frontend"));
    assert!(group_attrs(&multi, "scan_000001/band_1").contains_key("frontend"));
}
