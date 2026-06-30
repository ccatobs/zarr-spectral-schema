//! Identity + provenance contract conformance (Rust binding) — Slice 5a (#13).
//!
//! Validates the SAME spec-emitted stores the Python binding does
//! (conformance/test_identity_contract.py) — the cross-binding parity leg. The
//! shared generator (`conformance/generate.py main()`) emits, into the target
//! dir: the post-#6 `l0.zarr` (carrying the new optional identity attrs), the
//! pre-#6 `l0_pre6.zarr` (without them), and the wrong-typed negatives
//! `l0_bad_raw_attrs.zarr` / `l0_bad_pixels_per_array.zarr`.
//!
//! Proves the contract is additive: post-#6 green, pre-#6 still green (fields
//! optional), wrong-typed rejected — including the legacy `pixels_per_array`
//! dict form, which `type: array` deliberately refuses.

use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};

use zarr_spectral_schema as zss;

static COUNTER: AtomicUsize = AtomicUsize::new(0);

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("canonicalize repo root")
}

/// Emit fixtures into a unique temp dir using the shared spec-driven generator.
fn emit_fixtures() -> PathBuf {
    let root = repo_root();
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    let out = std::env::temp_dir().join(format!("zss_rust_id_{}_{n}", std::process::id()));
    let _ = std::fs::remove_dir_all(&out);
    let status = Command::new("python3")
        .arg(root.join("conformance/generate.py"))
        .arg(&out)
        .status()
        .expect("run conformance/generate.py (needs python3 + zarr/numpy)");
    assert!(status.success(), "generator failed");
    out
}

// ── additive: post-#6 green; pre-#6 (no new fields) still green ──────────────

#[test]
fn post6_l0_is_green() {
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("l0.zarr"));
    assert!(problems.is_empty(), "post-#6 store must be green, got: {problems:?}");
}

#[test]
fn pre6_l0_is_green() {
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("l0_pre6.zarr"));
    assert!(
        problems.is_empty(),
        "pre-#6 store (optional fields absent) must still be green, got: {problems:?}"
    );
}

// ── wrong-typed values are rejected ─────────────────────────────────────────

#[test]
fn raw_attrs_must_be_object() {
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("l0_bad_raw_attrs.zarr"));
    assert!(
        problems
            .iter()
            .any(|p| p.contains("raw_attrs") && p.contains("object")),
        "expected raw_attrs type rejection, got: {problems:?}"
    );
}

#[test]
fn pixels_per_array_dict_form_is_rejected() {
    // The legacy HashMap/dict form must NOT validate: `type: array` rejects a
    // JSON object, so the non-deterministic dict representation cannot be lifted.
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("l0_bad_pixels_per_array.zarr"));
    assert!(
        problems
            .iter()
            .any(|p| p.contains("pixels_per_array") && p.contains("array")),
        "expected pixels_per_array type rejection, got: {problems:?}"
    );
}

// ── L1: frontend promoted to scan-or-band placement — both shapes green ──────

#[test]
fn l1_frontend_scan_or_band_is_green() {
    let f = emit_fixtures();
    assert!(zss::validate(&f.join("l1_single.zarr")).is_empty());
    assert!(zss::validate(&f.join("l1_multi.zarr")).is_empty());
}
